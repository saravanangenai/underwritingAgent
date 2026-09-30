# Deploying to a Google Cloud free-tier VM

One **e2-micro** VM (1 GB RAM) runs everything, without Docker:

```
browser ──HTTPS──> Caddy :443 ──┬── /            the built Vue workbench (static files)
                                └── /api/* ──> uvicorn 127.0.0.1:8000 (one worker) ──> SQLite
```

| Part | Runs as | Memory cap |
| --- | --- | --- |
| API (`underwriting-api.service`) | user `underwriting`, sandboxed | 384 MB (throttled from 320 MB) |
| Caddy (HTTPS, workbench, `/api` proxy) | user `caddy` | 128 MB |
| Nightly backup (`underwriting-backup.timer`) | user `underwriting` | 128 MB |
| Swap file | | 1 GB safety net |

Measured: the API idles at about 121 MB and peaked at 138 MB during 60 submissions and 15
reviews (`deploy/memcheck.py`, which CI runs on every change). The OS, Caddy and the API
together use roughly half the VM's memory.

> **Check the free tier yourself.** At the time of writing, Google's free tier includes one
> e2-micro VM per month in `us-west1`, `us-central1` or `us-east1`, 30 GB of standard
> persistent disk and 1 GB of outbound traffic (North America). Terms change, and some items
> (for example external IPv4 addresses) may be billed. Read the current
> [free tier page](https://cloud.google.com/free/docs/free-cloud-features) and **set a budget
> alert** (Billing → Budgets & alerts) before you start.

## What you need

- A Google Cloud project with billing enabled (the free tier still needs a billing account).
- The [gcloud CLI](https://cloud.google.com/sdk/docs/install), signed in: `gcloud auth login`.
- On your computer: this repository, `uv` and Node.js (to build the workbench).
- Your production API keys (Groq, and optionally Gemini and Apify). Use separate keys from
  your laptop's, so you can revoke one without breaking the other.

The examples use zone `us-central1-a` and VM name `underwriting-vm`.

## 1. Create the VM and open the web ports (once)

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud compute instances create underwriting-vm --zone=us-central1-a --machine-type=e2-micro --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=30GB --boot-disk-type=pd-standard --tags=web
gcloud compute firewall-rules create allow-web --allow=tcp:80,tcp:443 --target-tags=web
```

Ports 80 and 443 must be open for Caddy to get an HTTPS certificate from Let's Encrypt. SSH
(port 22) is already allowed by the default network; `gcloud compute ssh` manages the keys.

**The address.** Without a domain, `install.sh` uses `<external-ip>.sslip.io`, a free name
that resolves to your VM's IP, so HTTPS works straight away. The VM's default IP is
*ephemeral*: it survives reboots but can change if you **stop** the VM, and then the address
changes too. For a stable address, either reserve a static IP (check its price) or point your
own domain's A record at the VM and pass `--domain`.

## 2. Build the release bundle (on your computer)

```bash
uv run python deploy/package.py
```

This builds the workbench and writes `dist/underwriting-agent-<version>-<commit>.tar.gz`
(backend source, `uv.lock`, the deploy files and the built workbench; no tests, no `.env`).
The VM never needs Node or access to your private repository.

## 3. Copy it to the VM and install

Use the bundle's real file name in place of `<bundle>`:

```bash
gcloud compute scp dist/<bundle>.tar.gz deploy/install.sh underwriting-vm:~ --zone=us-central1-a
gcloud compute ssh underwriting-vm --zone=us-central1-a --command="sudo bash install.sh <bundle>.tar.gz --email you@example.com"
```

`--email` is optional (Let's Encrypt uses it for expiry notices). Add `--domain uw.example.com`
if you have your own domain. The first run takes a few minutes: it creates the swap file,
installs Caddy, uv and automatic security updates, creates the `underwriting` service user,
installs Python and the locked dependencies, and starts everything. It ends by printing the
URL.

## 4. Add your keys (once)

The first install creates `/etc/underwriting-agent/api.env` from `deploy/api.env.example`,
owned by root and readable only by the service. Fill it in on the VM:

```bash
gcloud compute ssh underwriting-vm --zone=us-central1-a
sudo nano /etc/underwriting-agent/api.env
sudo systemctl restart underwriting-api
```

Or write the file on your computer (start from `deploy/api.env.example`) and copy it up, so
no key is typed on the server:

```bash
gcloud compute scp api.env underwriting-vm:/tmp/api.env --zone=us-central1-a
gcloud compute ssh underwriting-vm --zone=us-central1-a --command="sudo install -m 640 -o root -g underwriting /tmp/api.env /etc/underwriting-agent/api.env && rm /tmp/api.env && sudo systemctl restart underwriting-api"
```

Then delete your local copy of the production file. Never commit it, and never put keys in
the VM's metadata or startup script (anyone with viewer access to the project can read those).

## 5. Create the first admin (once)

On the VM (you are asked for a password, 12+ characters, at a hidden prompt):

```bash
sudo uw-users create --username you --name "Your Name" --role admin
```

Open the URL `install.sh` printed, sign in, and add everyone else on the **Users** page.
`sudo uw-users list`, `set-password` and `deactivate` also work on the VM.

## Updating

Build a new bundle, copy it and run the installer again. Settings, users and data are kept;
the new release is installed next to the old ones and the API restarts on it.

```bash
uv run python deploy/package.py
gcloud compute scp dist/<bundle>.tar.gz deploy/install.sh underwriting-vm:~ --zone=us-central1-a
gcloud compute ssh underwriting-vm --zone=us-central1-a --command="sudo bash install.sh <bundle>.tar.gz"
```

**Roll back** to the previous release (the last three are kept in
`/opt/underwriting-agent/releases`):

```bash
ls -1t /opt/underwriting-agent/releases
sudo ln -sfn /opt/underwriting-agent/releases/<previous> /opt/underwriting-agent/current
sudo systemctl restart underwriting-api
```

## Backups

Every night at about 03:15 (VM time) the database is copied with SQLite's online backup API
(safe while the API runs), integrity-checked and kept for 14 days in
`/var/backups/underwriting-agent`.

```bash
sudo systemctl list-timers underwriting-backup.timer   # next run
sudo systemctl start underwriting-backup               # back up now
sudo ls -lh /var/backups/underwriting-agent
```

**Restore:**

```bash
sudo systemctl stop underwriting-api
sudo install -o underwriting -g underwriting -m 640 /var/backups/underwriting-agent/<backup>.db /var/lib/underwriting-agent/underwriting.db
sudo systemctl start underwriting-api
```

Then open the workbench and check the audit banner (or `/api/audit/verify`).

These backups live on the same disk as the database. For protection against losing the VM,
copy them off it, for example to a Cloud Storage bucket
(`gcloud storage cp /var/backups/underwriting-agent/*.db gs://your-bucket/`; the VM's service
account needs write access to the bucket).

## Checking on it

```bash
systemctl status underwriting-api caddy
journalctl -u underwriting-api -n 100          # API log
journalctl -u caddy -n 50                      # certificates, proxy errors
free -m                                        # memory and swap
systemctl show underwriting-api -p MemoryCurrent -p MemoryPeak
curl -s http://127.0.0.1:8000/health
```

## Security notes

- The API listens only on `127.0.0.1`; the internet reaches it through Caddy, which adds HSTS,
  a strict Content-Security-Policy (no inline scripts or styles), `nosniff` and
  `frame-ancestors 'none'`.
- The API runs as an unprivileged user with a read-only system (`ProtectSystem=strict`), a
  private `/tmp` and write access only to `/var/lib/underwriting-agent`.
- Session cookies are `Secure` in production (`COOKIE_SECURE=true`), httpOnly and SameSite=Lax.
- Automatic security updates are on (`unattended-upgrades`).
- Everything except `/api/health` requires sign-in; five failed sign-ins lock a username for
  five minutes.
