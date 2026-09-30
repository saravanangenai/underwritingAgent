"""Phase 10: the deployment bundle, backups and the production configuration files."""

import importlib.util
import sqlite3
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup_mod = _load("backup")
package_mod = _load("package")


# --- backups ---


def _make_db(path: Path, rows: int = 3) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE t (x INTEGER)")
        db.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])


def test_backup_copies_a_consistent_database(tmp_path):
    db = tmp_path / "live.db"
    _make_db(db, rows=5)
    target = backup_mod.backup(db, tmp_path / "backups", keep=14)
    assert target.name.startswith("underwriting-") and target.suffix == ".db"
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT count(*) FROM t").fetchone()[0] == 5
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_backup_keeps_only_the_newest(tmp_path):
    db = tmp_path / "live.db"
    _make_db(db)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    for day in range(5):
        backup_mod.backup(db, tmp_path / "b", keep=3, now=start + timedelta(days=day))
    names = sorted(p.name for p in (tmp_path / "b").glob("*.db"))
    assert names == [
        "underwriting-20260903T000000Z.db",
        "underwriting-20260904T000000Z.db",
        "underwriting-20260905T000000Z.db",
    ]


def test_backup_reads_the_path_from_database_url():
    url = "sqlite+aiosqlite:////var/lib/underwriting-agent/underwriting.db"
    assert backup_mod.db_path_from_url(url) == Path("/var/lib/underwriting-agent/underwriting.db")
    with pytest.raises(ValueError, match="Only SQLite"):
        backup_mod.db_path_from_url("postgresql://db/x")


def test_backup_of_a_missing_database_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_mod.backup(tmp_path / "nope.db", tmp_path / "b", keep=3)


# --- the release bundle ---


def test_bundle_contains_the_app_and_workbench_with_unix_line_endings(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html>")
    (web / "assets" / "app.js").write_text("console.log(1)")

    bundle = package_mod.package(web, tmp_path / "out")
    with tarfile.open(bundle) as tar:
        names = tar.getnames()
        top = names[0].split("/")[0]
        members = {n.removeprefix(f"{top}/"): tar.getmember(n) for n in names}
        install = tar.extractfile(f"{top}/deploy/install.sh").read()
        release = tar.extractfile(f"{top}/RELEASE").read().decode()

    assert top.startswith("underwriting-agent-")
    for required in [
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "src/underwriting_agent/api/main.py",
        "deploy/install.sh",
        "deploy/uw-users",
        "deploy/systemd/underwriting-api.service",
        "deploy/Caddyfile",
        "web/index.html",
        "web/assets/app.js",
    ]:
        assert required in members, required
    assert b"\r\n" not in install  # bash on the server would choke on \r
    assert members["deploy/install.sh"].mode == 0o755
    assert members["deploy/uw-users"].mode == 0o755
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert not any(n.endswith("/.env") or "/tests/" in n for n in names)  # no secrets or tests
    assert "version=" in release and "commit=" in release


def test_bundle_needs_a_built_workbench(tmp_path):
    with pytest.raises(SystemExit, match="No built workbench"):
        package_mod.package(tmp_path / "missing", tmp_path / "out")


# --- production configuration ---


def test_api_service_fits_the_free_tier_memory_budget():
    unit = (DEPLOY / "systemd" / "underwriting-api.service").read_text()
    assert "--workers 1" in unit
    assert "--host 127.0.0.1" in unit  # only Caddy on the same machine reaches it
    assert "MemoryMax=384M" in unit
    assert "--root-path /api" in unit
    assert "EnvironmentFile=/etc/underwriting-agent/api.env" in unit
    assert "User=underwriting" in unit and "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit


def test_caddy_serves_the_workbench_and_proxies_the_api():
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    assert "handle_path /api/*" in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert "try_files {path} /index.html" in caddyfile
    assert "Strict-Transport-Security" in caddyfile
    assert "MemoryMax=128M" in (DEPLOY / "systemd" / "caddy-memory.conf").read_text()


def test_production_settings_example_has_no_secrets_and_secure_cookies():
    env = (DEPLOY / "api.env.example").read_text()
    assert "COOKIE_SECURE=true" in env
    for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "APIFY_TOKEN"):
        assert f"{key}=\n" in env  # present but empty


def test_install_script_pins_uv_and_never_overwrites_settings():
    script = (DEPLOY / "install.sh").read_text()
    assert 'UV_VERSION="' in script
    assert 'if [[ ! -f "$CONF/api.env" ]]' in script
    assert "uv sync --frozen --no-dev" in script
