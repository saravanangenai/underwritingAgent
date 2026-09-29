from typing import Literal

from pydantic import BaseModel, Field


class RiskFlag(BaseModel):
    rule_id: str  # which guideline was applied, e.g. "SR-010"
    issue: str
    severity: Literal["info", "caution", "hard_stop"]
    source_field: str  # citation: the data field that triggered the flag
    origin: Literal["rule", "llm"] = "rule"  # evaluated in code, or raised by the LLM


class Recommendation(BaseModel):
    """The LLM's triage recommendation. Triage applies it only within the guardrails."""

    action: Literal["approve", "refer", "decline"]
    confidence: float = Field(ge=0, le=1)
    rationale: str


class RiskMemo(BaseModel):
    flags: list[RiskFlag]
    narrative: str
    composite_risk_score: float | None = None
    assessed_by: str = "rules"  # "rules", or the provider/model of the LLM assessment
    recommendation: Recommendation | None = None
    rejected_flags: list[str] = []  # LLM flags dropped by validation, kept for the audit trail
