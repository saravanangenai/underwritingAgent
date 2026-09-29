"""Triage agent: turns a risk memo into approve / decline / refer.

Phase 1 uses only deterministic rules. Later, an LLM may break ties in the ambiguous middle,
but hard stops will always be decided by code.
"""

from underwriting_agent.schemas import RiskMemo, Submission, TriageDecision
from underwriting_agent.tools.rating_mock import rating_quote


async def triage_agent(submission: Submission, memo: RiskMemo) -> TriageDecision:
    hard = [f.rule_id for f in memo.flags if f.severity == "hard_stop"]
    caution = [f.rule_id for f in memo.flags if f.severity == "caution"]

    if hard:
        return TriageDecision(
            action="auto_decline",
            confidence=1.0,  # decided by a deterministic rule
            rationale=f"Hard-stop rule(s) triggered: {', '.join(hard)}",
        )

    premium = await rating_quote(submission)
    if caution:
        return TriageDecision(
            action="refer_to_underwriter",
            rationale=f"Caution flag(s) need underwriter review: {', '.join(caution)}",
            estimated_premium=premium,
        )
    return TriageDecision(
        action="auto_approve",
        rationale="No caution or hard-stop flags",
        estimated_premium=premium,
    )
