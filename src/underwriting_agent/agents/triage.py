"""Triage agent: turns a risk memo into approve / decline / refer.

Guardrails, in order:
1. Any hard stop declines. Only code declines; the LLM can never decline or prevent a decline.
2. Any caution flag (from the rules or an accepted LLM flag) refers.
3. With an LLM recommendation: "refer" or "decline" refers, and "approve" approves only at
   or above rules.LLM_APPROVE_MIN_CONFIDENCE. So the LLM can make a decision more cautious,
   never less.
4. Without one, no flags means approve (the rules-only behaviour).
"""

from underwriting_agent import rules
from underwriting_agent.schemas import RiskMemo, Submission, TriageDecision
from underwriting_agent.tools.rating_mock import rating_quote


async def triage_agent(submission: Submission, memo: RiskMemo) -> TriageDecision:
    hard = [f.rule_id for f in memo.flags if f.severity == "hard_stop"]
    caution = [f.rule_id for f in memo.flags if f.severity == "caution"]
    rec = memo.recommendation

    if hard:
        return TriageDecision(
            action="auto_decline",
            confidence=1.0,  # decided by a deterministic rule
            rationale=f"Hard-stop rule(s) triggered: {', '.join(hard)}",
        )

    premium = await rating_quote(submission)
    if caution:
        rationale = f"Caution flag(s) need underwriter review: {', '.join(caution)}"
        if rec:
            rationale += f". LLM: {rec.rationale}"
        return TriageDecision(
            action="refer_to_underwriter",
            confidence=rec.confidence if rec and rec.action != "approve" else None,
            rationale=rationale,
            estimated_premium=premium,
        )

    if rec is None:
        return TriageDecision(
            action="auto_approve",
            rationale="No caution or hard-stop flags",
            estimated_premium=premium,
        )

    if rec.action == "approve" and rec.confidence >= rules.LLM_APPROVE_MIN_CONFIDENCE:
        return TriageDecision(
            action="auto_approve",
            confidence=rec.confidence,
            rationale=f"No flags; LLM approves: {rec.rationale}",
            estimated_premium=premium,
        )
    if rec.action == "approve":
        reason = (
            f"LLM approval confidence {rec.confidence:.2f} is below "
            f"{rules.LLM_APPROVE_MIN_CONFIDENCE}: {rec.rationale}"
        )
    elif rec.action == "decline":
        reason = (
            f"LLM suggests decline; only hard-stop rules decline, so referring: {rec.rationale}"
        )
    else:
        reason = f"LLM recommends review: {rec.rationale}"
    return TriageDecision(
        action="refer_to_underwriter",
        confidence=rec.confidence,
        rationale=reason,
        estimated_premium=premium,
    )
