"""Compliance agent: builds the audit record for a decision.

It only builds the record. The pipeline's caller persists it, which keeps this testable.
"""

from underwriting_agent import rules
from underwriting_agent.schemas import DecisionLogEntry, RiskMemo, Submission, TriageDecision
from underwriting_agent.utils import utcnow


async def compliance_agent(
    submission: Submission,
    memo: RiskMemo,
    decision: TriageDecision,
    intake_model: str | None = None,
) -> DecisionLogEntry:
    # Record every model that touched the decision, so the audit trail can be reproduced.
    risk_model = rules.MODEL_VERSION if memo.assessed_by == "rules" else memo.assessed_by
    parts = [f"intake={intake_model}"] if intake_model else []
    if parts or risk_model != rules.MODEL_VERSION:
        parts.append(f"risk={risk_model}")
    model_version = "; ".join(parts) or rules.MODEL_VERSION
    return DecisionLogEntry(
        submission_id=submission.submission_id,
        decision=decision,
        risk_memo=memo,
        model_version=model_version,
        rules_version=rules.RULES_VERSION,
        created_at=utcnow(),
    )
