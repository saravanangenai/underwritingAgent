"""The underwriting pipeline as a LangGraph StateGraph.

    START -> intake -- missing fields ----------------------------------> END
               |
               +-- complete: Send x N --> enrich (parallel) --> risk_assessment
                                                                     |
                                    END <-- compliance <-- triage <--+

Intake routes with a conditional edge. When the submission is complete, it fans out one
`Send("enrich", ...)` per lookup chosen for that submission; the lookups run in parallel in
one superstep, and the `enrichment` reducer merges their results before risk assessment runs.
"""

import operator
from typing import Annotated, Any, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from underwriting_agent.agents.compliance import compliance_agent
from underwriting_agent.agents.enrichment import HazardProvider, run_lookup, select_lookups
from underwriting_agent.agents.intake import intake_agent
from underwriting_agent.agents.risk_assessment import risk_assessment_agent
from underwriting_agent.agents.triage import triage_agent
from underwriting_agent.llm import LLM
from underwriting_agent.schemas import (
    DecisionLogEntry,
    EnrichmentResult,
    RiskMemo,
    Submission,
    TriageDecision,
)

log = structlog.get_logger()


class PipelineState(TypedDict, total=False):
    raw: dict[str, Any]
    submission_id: str
    submission: Submission | None
    missing_fields: list[str]
    intake_model: str | None
    intake_evidence: dict[str, dict[str, Any]]
    enrichment: Annotated[list[EnrichmentResult], operator.add]  # merged from parallel lookups
    risk_memo: RiskMemo
    decision: TriageDecision
    log_entry: DecisionLogEntry


class LookupTask(TypedDict):
    """The payload of one Send: which lookup to run, for which submission."""

    lookup: str
    submission: Submission


def build_graph(
    llm: LLM | None = None,
    hazard: HazardProvider | None = None,
    risk_llm: LLM | None = None,
):
    """Compile the pipeline.

    `llm` reads ACORD text, `hazard` is the live hazard provider and `risk_llm` reviews the
    risk after the rules run. Any of them may be None.
    """

    async def intake(state: PipelineState) -> dict[str, Any]:
        result = await intake_agent(state["raw"], state["submission_id"], llm)
        log.info(
            "agent_completed",
            agent="intake",
            submission_id=state["submission_id"],
            missing=len(result.missing_fields),
            model=result.model_id,
        )
        return {
            "submission": result.submission,
            "missing_fields": result.missing_fields,
            "intake_model": result.model_id,
            "intake_evidence": result.evidence,
        }

    def route_after_intake(state: PipelineState) -> str | list[Send]:
        submission = state.get("submission")
        if submission is None:
            return END  # nothing is enriched or decided on incomplete data
        return [
            Send("enrich", LookupTask(lookup=name, submission=submission))
            for name in select_lookups(submission)
        ]

    async def enrich(task: LookupTask) -> dict[str, Any]:
        result = await run_lookup(task["lookup"], task["submission"], hazard)
        log.info(
            "lookup_completed",
            lookup=task["lookup"],
            submission_id=task["submission"].submission_id,
            source=result.source,
        )
        return {"enrichment": [result]}

    async def risk_assessment(state: PipelineState) -> dict[str, Any]:
        enrichment = sorted(state["enrichment"], key=lambda r: r.source)  # stable output order
        memo = await risk_assessment_agent(state["submission"], enrichment, risk_llm)
        log.info(
            "agent_completed",
            agent="risk_assessment",
            submission_id=state["submission_id"],
            sources=[r.source for r in enrichment],
            flags=[f.rule_id for f in memo.flags],
            assessed_by=memo.assessed_by,
            rejected=len(memo.rejected_flags),
        )
        return {"risk_memo": memo}

    async def triage(state: PipelineState) -> dict[str, Any]:
        decision = await triage_agent(state["submission"], state["risk_memo"])
        log.info(
            "agent_completed",
            agent="triage",
            submission_id=state["submission_id"],
            action=decision.action,
        )
        return {"decision": decision}

    async def compliance(state: PipelineState) -> dict[str, Any]:
        entry = await compliance_agent(
            state["submission"],
            state["risk_memo"],
            state["decision"],
            intake_model=state.get("intake_model"),
            intake_evidence=state.get("intake_evidence"),
            enrichment=sorted(state["enrichment"], key=lambda r: r.source),
        )
        log.info("agent_completed", agent="compliance", submission_id=state["submission_id"])
        return {"log_entry": entry}

    graph = StateGraph(PipelineState)
    graph.add_node("intake", intake)
    graph.add_node("enrich", enrich)
    graph.add_node("risk_assessment", risk_assessment)
    graph.add_node("triage", triage)
    graph.add_node("compliance", compliance)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", route_after_intake, ["enrich", END])
    graph.add_edge("enrich", "risk_assessment")
    graph.add_edge("risk_assessment", "triage")
    graph.add_edge("triage", "compliance")
    graph.add_edge("compliance", END)
    return graph.compile()
