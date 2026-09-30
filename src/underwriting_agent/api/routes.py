import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.graph.orchestrator import run_pipeline
from underwriting_agent.llm import LLMError
from underwriting_agent.persistence import repository as repo
from underwriting_agent.review import REVIEW_OUTCOMES, REVIEWABLE_STATUSES, build_review_entry
from underwriting_agent.samples import SAMPLE_NAMES, load_sample
from underwriting_agent.schemas import (
    AuditVerification,
    DecisionLogEntry,
    NeedsMoreInfoResponse,
    ReviewRequest,
    SubmissionPage,
    SubmissionStatus,
    UnderwriteResponse,
)
from underwriting_agent.utils import utcnow

log = structlog.get_logger()
router = APIRouter()

_EXAMPLES = {
    name: {"summary": name.replace("_", " ").title(), "value": load_sample(name)}
    for name in SAMPLE_NAMES
}


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/underwrite",
    response_model=UnderwriteResponse,
    responses={
        409: {"description": "A submission with this submission_id already exists"},
        422: {"model": NeedsMoreInfoResponse, "description": "Required fields are missing"},
        503: {"description": "acord_text was sent but no LLM provider is configured or reachable"},
    },
)
async def underwrite(
    request: Request,
    raw: dict[str, Any] = Body(..., openapi_examples=_EXAMPLES),
    session: AsyncSession = Depends(get_session),
):
    """Run the underwriting pipeline on one submission.

    Send either structured JSON (see the samples) or `{"acord_text": "..."}` with the raw
    application text, which the LLM reads first.
    """
    submission_id = str(raw.get("submission_id") or f"SUB-{uuid.uuid4().hex[:8].upper()}")

    if await repo.get_submission(session, submission_id):
        raise HTTPException(status_code=409, detail=f"Submission {submission_id} already exists")
    try:
        await repo.create_submission(session, submission_id, raw)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"Submission {submission_id} already exists"
        ) from None

    try:
        state = request.app.state
        result = await run_pipeline(raw, submission_id, state.llm, state.hazard, state.risk_llm)
    except LLMError as exc:
        log.warning("llm_unavailable", submission_id=submission_id, error=str(exc))
        await repo.set_status(session, submission_id, "failed")
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception:
        log.exception("pipeline_failed", submission_id=submission_id)
        await repo.set_status(session, submission_id, "failed")
        raise HTTPException(status_code=500, detail="Underwriting pipeline failed") from None

    if result.log_entry is None:  # intake found missing fields
        await repo.set_status(session, submission_id, result.status)
        body = NeedsMoreInfoResponse(
            submission_id=submission_id, missing_fields=result.missing_fields
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    await repo.save_decision(session, result.log_entry)
    await repo.set_status(session, submission_id, result.status)
    return UnderwriteResponse(
        submission_id=submission_id,
        status=result.status,
        decision=result.decision,
        risk_memo=result.risk_memo,
        enrichment=result.enrichment,
    )


SUBMISSION_STATUSES = ("running", "completed", "referred", "declined", "needs_more_info", "failed")


@router.get("/submissions", response_model=SubmissionPage)
async def list_submissions(
    status: str | None = Query(None, description=f"One of: {', '.join(SUBMISSION_STATUSES)}"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """Submissions newest first, each with its latest decision summarised (the queue view)."""
    if status is not None and status not in SUBMISSION_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status {status!r}")
    return await repo.list_submissions(session, status=status, limit=limit, offset=offset)


@router.get("/submissions/{submission_id}", response_model=SubmissionStatus)
async def get_submission_status(submission_id: str, session: AsyncSession = Depends(get_session)):
    row = await repo.get_submission(session, submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return SubmissionStatus(
        submission_id=row.id,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/submissions/{submission_id}/decision", response_model=DecisionLogEntry)
async def get_submission_decision(submission_id: str, session: AsyncSession = Depends(get_session)):
    entry = await repo.get_decision(session, submission_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No decision recorded for this submission")
    return entry


@router.get("/audit/verify", response_model=AuditVerification)
async def verify_audit_log(session: AsyncSession = Depends(get_session)):
    """Recompute the decision log's hash chain. `ok` is false if any entry was edited,
    removed or reordered, and `first_bad_entry_id` points at the first broken entry."""
    return await repo.verify_chain(session)


@router.get("/submissions/{submission_id}/history", response_model=list[DecisionLogEntry])
async def get_submission_history(submission_id: str, session: AsyncSession = Depends(get_session)):
    """Every audit entry for the submission, oldest first: the automated decision, then any
    underwriter review."""
    if await repo.get_submission(session, submission_id) is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return await repo.get_history(session, submission_id)


@router.post(
    "/submissions/{submission_id}/review",
    response_model=DecisionLogEntry,
    responses={
        404: {"description": "Submission not found"},
        409: {"description": "The submission is not waiting for an underwriter"},
    },
)
async def review_submission(
    submission_id: str,
    body: ReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Record an underwriter's decision on a referred submission.

    Appends a new entry to the decision log (the automated decision stays in the chain) and
    moves the submission to completed, declined or needs_more_info.
    """
    row = await repo.get_submission(session, submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    if row.status not in REVIEWABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Only referred submissions can be reviewed; this one is {row.status}",
        )
    previous = await repo.get_decision(session, submission_id)
    if previous is None:
        raise HTTPException(status_code=409, detail="No decision recorded to review")

    entry = await repo.save_decision(session, build_review_entry(previous, body, utcnow()))
    _, status = REVIEW_OUTCOMES[body.action]
    await repo.set_status(session, submission_id, status)
    log.info(
        "underwriter_review",
        submission_id=submission_id,
        action=body.action,
        reviewer=body.reviewer,
        status=status,
    )
    return entry
