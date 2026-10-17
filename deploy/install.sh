#!/usr/bin/env bash
# Install or update the Underwriting Agent on a Debian 12 VM (e.g. a Google Cloud e2-micro).
#
#   sudo bash install.sh underwriting-agent-<version>.tar.gz [--domain uw.example.com] [--email you@example.com]
#
# Safe to re-run: the first run sets up swap, Caddy, uv, the service user and directories;
# every run installs the bundle as a new release, switches to it and restarts the API.
# Without --domain it uses <external-ip>.sslip.io, which resolves to this VM, so HTTPS works
# without buying a domain.
set -euo pipefail

UV_VERSION="0.12.21"
APP=/opt/underwriting-agent
DATA=/var/lib/underwriting-agent
BACKUPS=/var/backups/underwriting-agent
CONF=/etc/underwriting-agent
SERVICE_USER=underwriting
KEEP_RELEASES=3

say() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# ---- arguments ---------------------------------------------------------------------------------
BUNDLE="" DOMAIN="" EMAIL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:?--domain needs a value}"; shift 2 ;;
    --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) [[ -z "$BUNDLE" ]] || die "unexpected argument: $1"; BUNDLE="$1"; shift ;;
  esac
done
[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -f "$BUNDLE" ]] || die "bundle not found: ${BUNDLE:-<none>} (build it with deploy/package.py)"

# ---- swap: a safety net on a 1 GB machine ------------------------------------------------------
if ! swapon --show --noheadings | grep -q .; then
  say "Creating a 1 GB swap file"
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/90-underwriting-swap.conf
  sysctl -q -p /etc/sysctl.d/90-underwriting-swap.conf
fi

# ---- packages: Caddy from its official repository ----------------------------------------------
if ! command -v caddy >/dev/null; then
  say "Installing Caddy"
  apt-get update -q
  apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

# ---- automatic security updates (the VM is on the internet) -------------------------------------
if ! dpkg -s unattended-upgrades >/dev/null 2>&1; then
  say "Enabling automatic security updates"
  apt-get install -y -q unattended-upgrades
  printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    > /etc/apt/apt.conf.d/20auto-upgrades
fi

# ---- uv, pinned -------------------------------------------------------------------------------
if [[ "$(/usr/local/bin/uv --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
  say "Installing uv $UV_VERSION"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" \
    | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

# ---- service user and directories -------------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  say "Creating the $SERVICE_USER service user"
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -m 755 "$APP" "$APP/releases" "$APP/python"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA" "$BACKUPS"
install -d -m 750 -o root -g "$SERVICE_USER" "$CONF"

# ---- unpack the release ------------------------------------------------------------------------
say "Installing $(basename "$BUNDLE")"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$BUNDLE" -C "$TMP"
SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)
[[ -f "$SRC/pyproject.toml" && -f "$SRC/web/index.html" ]] || die "not a release bundle: $BUNDLE"
RELEASE="$APP/releases/$(basename "$SRC")-$STAMP"
mv "$SRC" "$RELEASE"
chown -R root:root "$RELEASE"
chmod -R u=rwX,go=rX "$RELEASE"

say "Installing Python and dependencies (uv sync --frozen)"
(
  cd "$RELEASE"
  UV_PYTHON_INSTALL_DIR="$APP/python" UV_LINK_MODE=copy UV_CACHE_DIR=/var/cache/uv \
    /usr/local/bin/uv sync --frozen --no-dev --no-editable --compile-bytecode --quiet
)
chmod -R go+rX "$APP/python" "$RELEASE/.venv"

# ---- settings (created once; never overwritten) -----------------------------------------------
NEED_KEYS=0
if [[ ! -f "$CONF/api.env" ]]; then
  install -m 640 -o root -g "$SERVICE_USER" "$RELEASE/deploy/api.env.example" "$CONF/api.env"
  NEED_KEYS=1
fi

# ---- Caddy site ------------------------------------------------------------------------------
if [[ -z "$DOMAIN" && -f "$CONF/domain" ]]; then DOMAIN=$(cat "$CONF/domain"); fi
if [[ -z "$DOMAIN" ]]; then
  IP=$(curl -fsS -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip \
    || true)
  [[ -n "$IP" ]] || die "could not find this VM's external IP; pass --domain"
  DOMAIN="${IP//./-}.sslip.io"
fi
echo "$DOMAIN" > "$CONF/domain"
if [[ -z "$EMAIL" && -f "$CONF/email" ]]; then EMAIL=$(cat "$CONF/email"); fi
[[ -n "$EMAIL" ]] && echo "$EMAIL" > "$CONF/email"

say "Configuring Caddy for https://$DOMAIN"
if [[ -n "$EMAIL" ]]; then
  sed -e "s|__DOMAIN__|$DOMAIN|" -e "s|__EMAIL__|$EMAIL|" "$RELEASE/deploy/Caddyfile" > /etc/caddy/Caddyfile.new
else
  # No email: drop the global options block (Let's Encrypt works without one).
  sed -e "s|__DOMAIN__|$DOMAIN|" -e '/^{$/,/^}$/d' "$RELEASE/deploy/Caddyfile" > /etc/caddy/Caddyfile.new
fi
install -d -m 755 -o caddy -g caddy /var/log/caddy
caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile.new >/dev/null
# validate runs as root and creates the access log; the caddy service must own it.
chown -R caddy:caddy /var/log/caddy
mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
install -d -m 755 /etc/systemd/system/caddy.service.d
install -m 644 "$RELEASE/deploy/systemd/caddy-memory.conf" /etc/systemd/system/caddy.service.d/memory.conf

# ---- switch to the new release and (re)start ------------------------------------------------------
ln -sfn "$RELEASE" "$APP/current.new"
mv -T "$APP/current.new" "$APP/current"

install -m 644 "$RELEASE/deploy/systemd/underwriting-api.service" /etc/systemd/system/
install -m 644 "$RELEASE/deploy/systemd/underwriting-backup.service" /etc/systemd/system/
install -m 644 "$RELEASE/deploy/systemd/underwriting-backup.timer" /etc/systemd/system/
install -m 755 "$RELEASE/deploy/uw-users" /usr/local/bin/uw-users
systemctl daemon-reload
systemctl enable --quiet underwriting-api.service underwriting-backup.timer caddy.service
systemctl start underwriting-backup.timer
systemctl restart underwriting-api.service
systemctl reload-or-restart caddy.service

say "Waiting for the API"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done
if [[ "${OK:-0}" != 1 ]]; then
  journalctl -u underwriting-api --no-pager -n 30
  die "the API did not start; the log is above"
fi

# ---- keep the last few releases ------------------------------------------------------------
ls -1dt "$APP"/releases/*/ | tail -n +$((KEEP_RELEASES + 1)) | xargs -r rm -rf

say "Done"
echo "  Workbench:  https://$DOMAIN"
echo "  API health: https://$DOMAIN/api/health"
echo "  Memory:     $(free -m | awk '/^Mem:/ {print $3 " MB used of " $2 " MB"}')"
if [[ $NEED_KEYS == 1 ]]; then
  echo
  echo "  Next: add your keys, then restart the API:"
  echo "    sudo nano $CONF/api.env"
  echo "    sudo systemctl restart underwriting-api"
fi
if [[ "$(uw-users list 2>/dev/null | grep -c ' admin ')" == 0 ]]; then
  echo
  echo "  Next: create the first admin (you will be asked for a password):"
  echo "    sudo uw-users create --username <you> --name \"<Your Name>\" --role admin"
fi
