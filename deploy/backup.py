"""Back up the SQLite database with SQLite's online backup API and keep the newest N copies.

Safe while the API is running: the backup API copies a consistent snapshot, unlike cp.

    python deploy/backup.py --dest /var/backups/underwriting-agent --keep 14

Reads DATABASE_URL (sqlite+aiosqlite:///path) from the environment, or --db.
Restore: stop the API, copy a backup over the database file, start the API, then check
/api/audit/verify.
"""

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PREFIX = "underwriting-"


def db_path_from_url(url: str) -> Path:
    for scheme in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(scheme):
            return Path(url[len(scheme) :])
    raise ValueError(f"Only SQLite database URLs can be backed up, not {url.split(':')[0]}")


def backup(db: Path, dest: Path, keep: int, now: datetime | None = None) -> Path:
    if not db.exists():
        raise FileNotFoundError(f"Database not found: {db}")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    target = dest / f"{PREFIX}{stamp}.db"
    partial = target.with_suffix(".db.partial")
    # closing(): a sqlite3 connection's own context manager commits but does not close.
    with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(partial)) as copy:
        source.backup(copy)
    with closing(sqlite3.connect(partial)) as copy:
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The backup failed its integrity check")
    partial.replace(target)  # only complete, checked backups get the final name

    backups = sorted(dest.glob(f"{PREFIX}*.db"))
    for old in backups[:-keep]:
        old.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="database file (default: from DATABASE_URL)")
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    try:
        db = Path(args.db) if args.db else db_path_from_url(os.environ["DATABASE_URL"])
        target = backup(db, args.dest, max(1, args.keep))
    except (KeyError, ValueError, FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backed up {db} to {target} ({target.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
