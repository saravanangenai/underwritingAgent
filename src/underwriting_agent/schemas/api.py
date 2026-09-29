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
