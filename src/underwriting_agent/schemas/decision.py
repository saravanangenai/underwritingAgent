from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from underwriting_agent.schemas.risk_memo import RiskMemo

Action = Literal[
    "auto_approve",
    "auto_decline",
    "refer_to_underwriter",
    # An underwriter's decision on a referred submission (see UnderwriterReview).
    "underwriter_approve",
    "underwriter_decline",
    "underwriter_request_info",
]
ReviewAction = Literal["approve", "decline", "request_info"]


class TriageDecision(BaseModel):
    action: Action
    confidence: float | None = None  # None when only rules were involved in the decision
    rationale: str
    estimated_premium: float | None = None  # MOCK value until a real rating engine is wired in


class ReviewRequest(BaseModel):
    """An underwriter's decision on a referred submission."""

    action: ReviewAction
    note: str = Field(min_length=10, max_length=4000)  # why: required for the audit trail
    # Until sign-in exists this is typed by the underwriter; it is not verified.
    reviewer: str = Field(min_length=2, max_length=100)

    @field_validator("note", "reviewer")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class UnderwriterReview(BaseModel):
    action: ReviewAction
    note: str
    reviewer: str
    reviewed_at: datetime
    supersedes_entry_hash: str | None  # the decision this review resolves


class DecisionLogEntry(BaseModel):
    """One audit record. With `inputs`, `prompt_versions` and `llm_review_raw` a decision can
    be replayed; `prev_hash` and `entry_hash` chain the records so edits are detectable."""

    submission_id: str
    decision: TriageDecision
    risk_memo: RiskMemo
    model_version: str
    rules_version: str
    created_at: datetime
    inputs: dict[str, Any] = {}  # the submission and enrichment exactly as the agents saw them
    prompt_versions: dict[str, str] = {}  # short hashes of the system prompts in force
    llm_review_raw: dict[str, Any] | None = None  # the LLM risk reply before validation
    review: UnderwriterReview | None = None  # set on entries recorded by an underwriter
    prev_hash: str | None = None  # set when saved
    entry_hash: str | None = None  # set when saved


class AuditVerification(BaseModel):
    ok: bool
    entries_checked: int
    first_bad_entry_id: int | None = None
    reason: str | None = None
