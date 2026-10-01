"""Build a release bundle to copy to the server.

    uv run python deploy/package.py            # builds the workbench, then the bundle
    uv run python deploy/package.py --skip-web-build   # reuse frontend/dist

Writes dist/underwriting-agent-<version>-<commit>.tar.gz containing the backend source,
uv.lock, the deploy files and the built workbench (web/). The server never needs Node or
access to the (private) Git repository: it installs from this file with deploy/install.sh.
"""

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["pyproject.toml", "uv.lock", ".python-version", "README.md", "src", "deploy"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        return f"{out}-dirty" if dirty else out
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def build_web() -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit(
            "npm not found; install Node.js or pass --skip-web-build with an existing build."
        )
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)


def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if SKIP_PARTS.intersection(Path(info.name).parts) or info.name.endswith(".pyc"):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def _add_deploy_files(tar: tarfile.TarFile, name: str) -> None:
    """Add deploy/ with Unix line endings: a Windows checkout may have \\r\\n, which breaks
    bash and systemd on the server. Scripts are marked executable."""
    for path in sorted((ROOT / "deploy").rglob("*")):
        if path.is_dir() or SKIP_PARTS.intersection(path.parts) or path.suffix == ".pyc":
            continue
        data = path.read_bytes().replace(b"\r\n", b"\n")
        info = tarfile.TarInfo(f"{name}/{path.relative_to(ROOT).as_posix()}")
        info.size = len(data)
        info.mode = 0o755 if path.suffix == ".sh" or path.name == "uw-users" else 0o644
        info.mtime = int(path.stat().st_mtime)
        info.uname = info.gname = "root"
        tar.addfile(info, io.BytesIO(data))


def package(web_dir: Path, out_dir: Path) -> Path:
    if not (web_dir / "index.html").exists():
        raise SystemExit(f"No built workbench in {web_dir}; run without --skip-web-build.")
    name = f"underwriting-agent-{version()}-{commit()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"{name}.tar.gz"
    stamp = f"version={version()}\ncommit={commit()}\nbuilt={datetime.now(UTC).isoformat()}\n"
    stamp_file = out_dir / "RELEASE"
    stamp_file.write_text(stamp, encoding="utf-8")
    with tarfile.open(bundle, "w:gz") as tar:
        for item in INCLUDE:
            if item == "deploy":
                _add_deploy_files(tar, name)
            else:
                tar.add(ROOT / item, arcname=f"{name}/{item}", filter=_filter)
        tar.add(web_dir, arcname=f"{name}/web", filter=_filter)
        tar.add(stamp_file, arcname=f"{name}/RELEASE", filter=_filter)
    stamp_file.unlink()
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a release bundle for deploy/install.sh")
    parser.add_argument(
        "--skip-web-build", action="store_true", help="use the existing frontend/dist"
    )
    parser.add_argument("--web-dir", type=Path, default=ROOT / "frontend" / "dist")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if not args.skip_web_build:
        build_web()
    bundle = package(args.web_dir, args.out)
    size = bundle.stat().st_size / 1024
    print(f"\nBundle: {bundle} ({size:.0f} KiB)")
    print("Next: copy it and deploy/install.sh to the server (see deploy/README.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
