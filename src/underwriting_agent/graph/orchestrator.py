"""Runs the five agents in order. Stops early if intake finds missing fields."""

from typing import Any

import structlog

from underwriting_agent.agents.compliance import compliance_agent
from underwriting_agent.agents.enrichment import enrichment_agent
from underwriting_agent.agents.intake import intake_agent
from underwriting_agent.agents.risk_assessment import risk_assessment_agent
from underwriting_agent.agents.triage import triage_agent
from underwriting_agent.graph.state import STATUS_FOR_ACTION, PipelineResult
from underwriting_agent.llm import LLM

log = structlog.get_logger()


async def run_pipeline(
    raw: dict[str, Any], submission_id: str, llm: LLM | None = None
) -> PipelineResult:
    intake = await intake_agent(raw, submission_id, llm)
    log.info(
        "agent_completed",
        agent="intake",
        submission_id=submission_id,
        missing=len(intake.missing_fields),
        model=intake.model_id,
    )
    if intake.submission is None:
        # Nothing is enriched or decided on incomplete data.
        return PipelineResult(
            submission_id=submission_id,
            status="needs_more_info",
            missing_fields=intake.missing_fields,
        )
    submission = intake.submission

    enrichment = await enrichment_agent(submission)
    log.info(
        "agent_completed",
        agent="enrichment",
        submission_id=submission_id,
        sources=[r.source for r in enrichment],
    )

    memo = await risk_assessment_agent(submission, enrichment)
    log.info(
        "agent_completed",
        agent="risk_assessment",
        submission_id=submission_id,
        flags=[f.rule_id for f in memo.flags],
    )

    decision = await triage_agent(submission, memo)
    log.info("agent_completed", agent="triage", submission_id=submission_id, action=decision.action)

    entry = await compliance_agent(submission, memo, decision, intake_model=intake.model_id)
    log.info("agent_completed", agent="compliance", submission_id=submission_id)

    return PipelineResult(
        submission_id=submission_id,
        status=STATUS_FOR_ACTION[decision.action],
        submission=submission,
        enrichment=enrichment,
        risk_memo=memo,
        decision=decision,
        log_entry=entry,
    )
