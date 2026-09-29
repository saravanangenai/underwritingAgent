"""Runs the LangGraph pipeline (graph/builder.py) and returns a PipelineResult."""

from typing import Any

from underwriting_agent.agents.enrichment import HazardProvider
from underwriting_agent.graph.builder import build_graph
from underwriting_agent.graph.state import STATUS_FOR_ACTION, PipelineResult
from underwriting_agent.llm import LLM


async def run_pipeline(
    raw: dict[str, Any],
    submission_id: str,
    llm: LLM | None = None,
    hazard: HazardProvider | None = None,
    risk_llm: LLM | None = None,
) -> PipelineResult:
    graph = build_graph(llm, hazard, risk_llm)
    state = await graph.ainvoke({"raw": raw, "submission_id": submission_id, "enrichment": []})

    decision = state.get("decision")
    if decision is None:  # intake found missing fields and the graph ended early
        return PipelineResult(
            submission_id=submission_id,
            status="needs_more_info",
            missing_fields=state.get("missing_fields", []),
        )
    return PipelineResult(
        submission_id=submission_id,
        status=STATUS_FOR_ACTION[decision.action],
        submission=state["submission"],
        enrichment=sorted(state["enrichment"], key=lambda r: r.source),
        risk_memo=state["risk_memo"],
        decision=decision,
        log_entry=state["log_entry"],
    )
