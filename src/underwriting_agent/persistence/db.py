from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SubmissionRow(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]
    raw_payload: Mapped[str] = mapped_column(Text)  # the request body as JSON
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class DecisionLogRow(Base):
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


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create tables if they do not exist. Fine for local SQLite; use migrations for SQL Server."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
