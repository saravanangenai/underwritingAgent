from datetime import datetime

import structlog
from sqlalchemy import Boolean, ForeignKey, String, Text, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from underwriting_agent.utils import utcnow

log = structlog.get_logger()


class Base(DeclarativeBase):
    pass


class SubmissionRow(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]
    raw_payload: Mapped[str] = mapped_column(Text)  # the request body as JSON
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    submitted_by: Mapped[str | None] = mapped_column(String(64))  # username (Phase 9)


class UserRow(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))  # viewer | underwriter | admin
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]


class SessionRow(Base):
    """A signed-in session. Only a SHA-256 of the token is stored."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), index=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class DecisionLogRow(Base):
    """Append-only audit log. Each row's entry_hash covers its content and prev_hash."""

    __tablename__ = "decision_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    action: Mapped[str]
    confidence: Mapped[float | None]
    rationale: Mapped[str] = mapped_column(Text)
    estimated_premium: Mapped[float | None]
    risk_memo_json: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str]
    rules_version: Mapped[str]
    created_at: Mapped[datetime]
    inputs_json: Mapped[str] = mapped_column(Text)
    prompt_versions_json: Mapped[str] = mapped_column(Text)
    llm_review_raw_json: Mapped[str | None] = mapped_column(Text)
    review_json: Mapped[str | None] = mapped_column(Text)  # underwriter review (Phase 8)
    # unique: two rows claiming the same predecessor would fork the chain
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _set_aside_legacy_log(sync_conn) -> None:
    """A decision_log from before Phase 5 has no hash columns and cannot join the chain.

    Rename it rather than drop it, so no audit history is lost.
    """
    inspector = inspect(sync_conn)
    if "decision_log" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("decision_log")}
    if "entry_hash" in columns:
        return
    # The old index names would clash with the new table's, so drop them first.
    for index in inspector.get_indexes("decision_log"):
        sync_conn.exec_driver_sql(f"DROP INDEX IF EXISTS {index['name']}")
    legacy = f"decision_log_legacy_{utcnow():%Y%m%d%H%M%S}"
    sync_conn.exec_driver_sql(f"ALTER TABLE decision_log RENAME TO {legacy}")
    log.warning("legacy_decision_log_renamed", table=legacy)


# Nullable columns added to existing tables after they were first created, applied in place.
# Old rows keep NULL; entry_hash leaves an empty review out, so decision hashes still verify.
_ADDED_COLUMNS = {
    "decision_log": {"review_json": "TEXT"},  # Phase 8
    "submissions": {"submitted_by": "VARCHAR(64)"},  # Phase 9
}


def _add_missing_columns(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, sql_type in columns.items():
            if name not in existing:
                sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                log.info("column_added", table=table, column=name)


async def init_db(engine: AsyncEngine) -> None:
    """Create tables if they do not exist. Fine for local SQLite; use migrations for SQL Server."""
    async with engine.begin() as conn:
        await conn.run_sync(_set_aside_legacy_log)
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(Base.metadata.create_all)
