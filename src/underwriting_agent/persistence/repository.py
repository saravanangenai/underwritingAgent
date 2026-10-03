import asyncio
import hashlib
import json
import weakref
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.persistence.db import DecisionLogRow, HazardResultRow, SubmissionRow
from underwriting_agent.schemas import (
    AuditVerification,
    DecisionLogEntry,
    EnrichmentResult,
    RiskMemo,
    SubmissionPage,
    SubmissionSummary,
    TriageDecision,
    UnderwriterReview,
)
from underwriting_agent.utils import utcnow

GENESIS_HASH = "0" * 64  # prev_hash of the first entry in the chain

# Reading the chain head and appending must not interleave within this process. Across
# processes, the unique prev_hash column rejects a second row that claims the same head.
# One lock per event loop, since an asyncio.Lock must not be shared between loops.
_append_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _append_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _append_locks.get(loop)
    if lock is None:
        lock = _append_locks[loop] = asyncio.Lock()
    return lock


async def create_submission(
    session: AsyncSession,
    submission_id: str,
    raw: dict[str, Any],
    submitted_by: str | None = None,
) -> None:
    now = utcnow()
    session.add(
        SubmissionRow(
            id=submission_id,
            status="running",
            raw_payload=json.dumps(raw),
            created_at=now,
            updated_at=now,
            submitted_by=submitted_by,
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


async def list_submissions(
    session: AsyncSession, status: str | None = None, limit: int = 50, offset: int = 0
) -> SubmissionPage:
    """Newest first, with each submission's latest decision summarised for the queue."""
    query = select(SubmissionRow)
    count = select(func.count()).select_from(SubmissionRow)
    if status:
        query = query.where(SubmissionRow.status == status)
        count = count.where(SubmissionRow.status == status)
    total = (await session.execute(count)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(SubmissionRow.created_at.desc(), SubmissionRow.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    latest: dict[str, DecisionLogRow] = {}
    if rows:
        decisions = await session.execute(
            select(DecisionLogRow)
            .where(DecisionLogRow.submission_id.in_([r.id for r in rows]))
            .order_by(DecisionLogRow.id)
        )
        for decision in decisions.scalars():
            latest[decision.submission_id] = decision  # later ids overwrite earlier ones

    items = []
    for row in rows:
        summary = SubmissionSummary(
            submission_id=row.id,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            submitted_by=row.submitted_by,
        )
        decision = latest.get(row.id)
        if decision is not None:
            memo = RiskMemo.model_validate_json(decision.risk_memo_json)
            inputs = json.loads(decision.inputs_json)
            summary.property_address = inputs.get("submission", {}).get("property_address")
            summary.action = decision.action
            summary.confidence = decision.confidence
            summary.flags = [f.rule_id for f in memo.flags if f.severity != "info"]
            summary.assessed_by = memo.assessed_by
            if decision.review_json:
                summary.reviewed_by = UnderwriterReview.model_validate_json(
                    decision.review_json
                ).reviewer
        items.append(summary)
    return SubmissionPage(items=items, total=total, limit=limit, offset=offset)


# --- decision log (hash chained) ---------------------------------------------------------


def entry_hash(entry: DecisionLogEntry, prev_hash: str) -> str:
    """SHA-256 over the previous hash and a canonical JSON form of the entry's content."""
    exclude = {"prev_hash", "entry_hash"}
    if entry.review is None:
        exclude.add("review")  # entries hashed before reviews existed have no such key
    content = entry.model_dump(mode="json", exclude=exclude)
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{prev_hash}\n{canonical}".encode()).hexdigest()


def _row_to_entry(row: DecisionLogRow) -> DecisionLogEntry:
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
        inputs=json.loads(row.inputs_json),
        prompt_versions=json.loads(row.prompt_versions_json),
        llm_review_raw=json.loads(row.llm_review_raw_json) if row.llm_review_raw_json else None,
        review=UnderwriterReview.model_validate_json(row.review_json) if row.review_json else None,
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
    )


async def _chain_head(session: AsyncSession) -> str:
    result = await session.execute(
        select(DecisionLogRow.entry_hash).order_by(DecisionLogRow.id.desc()).limit(1)
    )
    return result.scalar_one_or_none() or GENESIS_HASH


async def save_decision(session: AsyncSession, entry: DecisionLogEntry) -> DecisionLogEntry:
    """Append the entry to the chain and return it with prev_hash and entry_hash set."""
    async with _append_lock():
        prev = await _chain_head(session)
        # Hash exactly what will be read back: the stored memo drops raw_review.
        stored = entry.model_copy(
            update={"risk_memo": RiskMemo.model_validate_json(entry.risk_memo.model_dump_json())}
        )
        digest = entry_hash(stored, prev)
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
                inputs_json=json.dumps(entry.inputs),
                prompt_versions_json=json.dumps(entry.prompt_versions),
                llm_review_raw_json=(
                    json.dumps(entry.llm_review_raw) if entry.llm_review_raw is not None else None
                ),
                review_json=entry.review.model_dump_json() if entry.review else None,
                prev_hash=prev,
                entry_hash=digest,
            )
        )
        await session.commit()
    return stored.model_copy(update={"prev_hash": prev, "entry_hash": digest})


async def save_hazard_result(
    session: AsyncSession, submission_id: str, result: EnrichmentResult
) -> None:
    """Store one Apify hazard lookup result (including an "unavailable" one) for querying."""
    d = result.data
    top = d.get("top_hazards")
    session.add(
        HazardResultRow(
            submission_id=submission_id,
            source=result.source,
            status=d.get("status", "ok"),
            error=d.get("error"),
            composite_score=d.get("composite_score"),
            tier=d.get("tier"),
            expected_annual_loss_rating=d.get("expected_annual_loss_rating"),
            top_hazards_json=json.dumps(top) if top is not None else None,
            resolution=d.get("resolution"),
            tract_fips=d.get("tract_fips"),
            county=d.get("county"),
            state=d.get("state"),
            nri_version=d.get("nri_version"),
            geocoded_address=d.get("geocoded_address"),
            apify_actor=d.get("apify_actor"),
            apify_run_id=d.get("apify_run_id"),
            data_json=json.dumps(d),
            retrieved_at=result.retrieved_at,
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
    return _row_to_entry(row) if row else None


async def get_history(session: AsyncSession, submission_id: str) -> list[DecisionLogEntry]:
    """Every audit entry for one submission, oldest first."""
    result = await session.execute(
        select(DecisionLogRow)
        .where(DecisionLogRow.submission_id == submission_id)
        .order_by(DecisionLogRow.id)
    )
    return [_row_to_entry(row) for row in result.scalars()]


async def verify_chain(session: AsyncSession) -> AuditVerification:
    """Recompute every hash in order. Detects edited, deleted, reordered or inserted rows."""
    result = await session.execute(select(DecisionLogRow).order_by(DecisionLogRow.id))
    expected_prev = GENESIS_HASH
    count = 0
    for row in result.scalars():
        count += 1
        if row.prev_hash != expected_prev:
            return AuditVerification(
                ok=False,
                entries_checked=count,
                first_bad_entry_id=row.id,
                reason="prev_hash does not match the previous entry (a row was removed or "
                "reordered)",
            )
        try:
            recomputed = entry_hash(_row_to_entry(row), row.prev_hash)
        except (ValueError, TypeError) as exc:
            return AuditVerification(
                ok=False,
                entries_checked=count,
                first_bad_entry_id=row.id,
                reason=f"entry is unreadable: {type(exc).__name__}",
            )
        if recomputed != row.entry_hash:
            return AuditVerification(
                ok=False,
                entries_checked=count,
                first_bad_entry_id=row.id,
                reason="content does not match entry_hash (the row was edited)",
            )
        expected_prev = row.entry_hash
    return AuditVerification(ok=True, entries_checked=count)
