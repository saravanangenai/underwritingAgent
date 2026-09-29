"""Compliance agent: builds the audit record for a decision.

It only builds the record. The pipeline's caller persists it (the repository adds the hash
chain), which keeps this testable. The record carries everything needed to replay the
decision: the inputs the agents saw, the prompt versions and the LLM's raw risk reply.
"""

from underwriting_agent import rules
from underwriting_agent.agents.intake import ACORD_PROMPT_VERSION
from underwriting_agent.agents.risk_assessment import RISK_PROMPT_VERSION
from underwriting_agent.schemas import (
    DecisionLogEntry,
    EnrichmentResult,
    RiskMemo,
    Submission,
    TriageDecision,
)
from underwriting_agent.utils import utcnow


async def compliance_agent(
    submission: Submission,
    memo: RiskMemo,
    decision: TriageDecision,
    intake_model: str | None = None,
    enrichment: list[EnrichmentResult] | None = None,
) -> DecisionLogEntry:
    # Record every model that touched the decision, so the audit trail can be reproduced.
    risk_model = rules.MODEL_VERSION if memo.assessed_by == "rules" else memo.assessed_by
    parts = [f"intake={intake_model}"] if intake_model else []
    if parts or risk_model != rules.MODEL_VERSION:
        parts.append(f"risk={risk_model}")
    model_version = "; ".join(parts) or rules.MODEL_VERSION

    prompt_versions = {}
    if intake_model:
        prompt_versions["acord_intake"] = ACORD_PROMPT_VERSION
    if memo.assessed_by != "rules":
        prompt_versions["risk_review"] = RISK_PROMPT_VERSION

    return DecisionLogEntry(
        submission_id=submission.submission_id,
        decision=decision,
        risk_memo=memo,
        model_version=model_version,
        rules_version=rules.RULES_VERSION,
        created_at=utcnow(),
        inputs={
            "submission": submission.model_dump(mode="json"),
            "enrichment": [r.model_dump(mode="json") for r in enrichment or []],
        },
        prompt_versions=prompt_versions,
        llm_review_raw=memo.raw_review,
    )
