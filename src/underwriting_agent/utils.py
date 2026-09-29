import hashlib
from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (SQLite does not store timezones)."""
    return datetime.now(UTC).replace(tzinfo=None)


def prompt_version(prompt: str) -> str:
    """Short, stable id for a prompt's exact text, recorded with each decision."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
