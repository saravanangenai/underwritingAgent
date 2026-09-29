from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from underwriting_agent.schemas.risk_memo import RiskMemo

Action = Literal["auto_approve", "auto_decline", "refer_to_underwriter"]


class TriageDecision(BaseModel):
    action: Action
    confidence: float | None = None  # None until an LLM is involved in the decision
    rationale: str
    estimated_premium: float | None = None  # MOCK value in Phase 1


class DecisionLogEntry(BaseModel):
    submission_id: str
    decision: TriageDecision
    risk_memo: RiskMemo
    model_version: str
    rules_version: str
    created_at: datetime
