import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.graph.orchestrator import run_pipeline
from underwriting_agent.llm import LLMError
from underwriting_agent.persistence import repository as repo
from underwriting_agent.samples import SAMPLE_NAMES, load_sample
from underwriting_agent.schemas import (
    DecisionLogEntry,
    NeedsMoreInfoResponse,
    SubmissionStatus,
    UnderwriteResponse,
)

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
