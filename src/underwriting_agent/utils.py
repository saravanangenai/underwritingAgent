from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (SQLite does not store timezones)."""
    return datetime.now(UTC).replace(tzinfo=None)
