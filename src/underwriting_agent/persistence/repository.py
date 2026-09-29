import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.persistence.db import DecisionLogRow, SubmissionRow
from underwriting_agent.schemas import DecisionLogEntry, RiskMemo, TriageDecision
from underwriting_agent.utils import utcnow


async def create_submission(session: AsyncSession, submission_id: str, raw: dict[str, Any]) -> None:
    now = utcnow()
    session.add(
        SubmissionRow(
            id=submission_id,
            status="running",
            raw_payload=json.dumps(raw),
            created_at=now,
            updated_at=now,
        )
    )
    await session.commit()


async def get_submission(session: AsyncSession, submission_id: str) -> SubmissionRow | None:
    return await session.get(SubmissionRow, submission_id)


async def set_status(session: AsyncSession, submission_id: str, status: str) -> None:
    row = await session.get(SubmissionRow, submission_id)
    if row is not None:
        row.status = status
        row.updated_at = utcnow()
        await session.commit()


async def save_decision(session: AsyncSession, entry: DecisionLogEntry) -> None:
    session.add(
        DecisionLogRow(
            submission_id=entry.submission_id,
            action=entry.decision.action,
            confidence=entry.decision.confidence,
            rationale=entry.decision.rationale,
            estimated_premium=entry.decision.estimated_premium,
            risk_memo_json=entry.risk_memo.model_dump_json(),
            model_version=entry.model_version,
            rules_version=entry.rules_version,
            created_at=entry.created_at,
        )
    )
    await session.commit()


async def get_decision(session: AsyncSession, submission_id: str) -> DecisionLogEntry | None:
    result = await session.execute(
        select(DecisionLogRow)
        .where(DecisionLogRow.submission_id == submission_id)
        .order_by(DecisionLogRow.id.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return DecisionLogEntry(
        submission_id=row.submission_id,
        decision=TriageDecision(
            action=row.action,
            confidence=row.confidence,
            rationale=row.rationale,
            estimated_premium=row.estimated_premium,
        ),
        risk_memo=RiskMemo.model_validate_json(row.risk_memo_json),
        model_version=row.model_version,
        rules_version=row.rules_version,
        created_at=row.created_at,
    )
