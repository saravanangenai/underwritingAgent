from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from underwriting_agent.schemas.decision import TriageDecision
from underwriting_agent.schemas.enrichment import EnrichmentResult
from underwriting_agent.schemas.risk_memo import RiskMemo


class UnderwriteResponse(BaseModel):
    submission_id: str
    status: str
    decision: TriageDecision
    risk_memo: RiskMemo
    enrichment: list[EnrichmentResult]


class NeedsMoreInfoResponse(BaseModel):
    submission_id: str
    status: Literal["needs_more_info"] = "needs_more_info"
    missing_fields: list[str]


class SubmissionStatus(BaseModel):
    submission_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class SubmissionSummary(BaseModel):
    """One row of the underwriter queue. Decision fields are None until a decision exists."""

    submission_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    property_address: str | None = None
    action: str | None = None
    confidence: float | None = None
    flags: list[str] = []  # caution and hard-stop rule ids, rules and LLM alike
    assessed_by: str | None = None
    reviewed_by: str | None = None  # underwriter who resolved a referral


class SubmissionPage(BaseModel):
    items: list[SubmissionSummary]
    total: int
    limit: int
    offset: int
