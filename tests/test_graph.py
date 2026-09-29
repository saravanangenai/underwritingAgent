import asyncio

from tests.fakes import FakeHazard
from tests.test_agents import make_submission
from underwriting_agent.agents import enrichment
from underwriting_agent.agents.enrichment import enrichment_agent
from underwriting_agent.agents.risk_assessment import risk_assessment_agent
from underwriting_agent.graph.builder import build_graph
from underwriting_agent.graph.orchestrator import run_pipeline
from underwriting_agent.samples import load_sample


def test_graph_has_the_five_agent_nodes():
    nodes = set(build_graph().get_graph().nodes)
    assert {"intake", "enrich", "risk_assessment", "triage", "compliance"} <= nodes


async def test_send_fans_out_one_task_per_selected_lookup(monkeypatch):
    monkeypatch.setattr(enrichment, "select_lookups", lambda sub: ["hazard", "property_data"])
    # builder imported the name directly, so patch it there too.
    from underwriting_agent.graph import builder

    monkeypatch.setattr(builder, "select_lookups", lambda sub: ["hazard", "property_data"])
    result = await run_pipeline(load_sample("clean"), "SUB-FAN")
    assert [r.source for r in result.enrichment] == ["hazard_mock", "property_data_mock"]


async def test_lookups_run_in_parallel():
    started, release = [], asyncio.Event()

    class SlowHazard(FakeHazard):
        async def lookup(self, submission):
            started.append("hazard")
            await release.wait()  # blocks until another lookup has also started
            return await super().lookup(submission)

    original = enrichment.property_data_lookup

    async def property_lookup(submission):
        started.append("property_data")
        release.set()
        return await original(submission)

    enrichment.property_data_lookup = property_lookup
    try:
        result = await asyncio.wait_for(
            run_pipeline(load_sample("clean"), "SUB-PAR", hazard=SlowHazard(score=10)), timeout=5
        )
    finally:
        enrichment.property_data_lookup = original
    # Sequential execution would deadlock on the event and hit the timeout instead.
    assert {"hazard", "property_data"} <= set(started)
    assert result.status == "completed"


async def test_live_hazard_score_drives_hz001_and_is_cited():
    hazard = FakeHazard(score=90)
    result = await run_pipeline(load_sample("clean"), "SUB-LIVE", hazard=hazard)
    assert hazard.calls == 1
    assert result.status == "referred"
    hz = [f for f in result.risk_memo.flags if f.rule_id == "HZ-001"]
    assert hz and hz[0].source_field == "hazard_apify.composite_score"
    assert "hazard_mock" not in {r.source for r in result.enrichment}


async def test_low_live_hazard_score_still_auto_approves():
    result = await run_pipeline(load_sample("clean"), "SUB-LOW", hazard=FakeHazard(score=20))
    assert result.decision.action == "auto_approve"


async def test_failed_live_lookup_is_unavailable_not_mock_and_refers():
    result = await run_pipeline(
        load_sample("clean"), "SUB-DOWN", hazard=FakeHazard(error="Apify MCP call failed")
    )
    hazard = next(r for r in result.enrichment if r.source == "hazard_apify")
    assert hazard.data == {"status": "unavailable", "error": "Apify MCP call failed"}
    assert "HZ-002" in {f.rule_id for f in result.risk_memo.flags}
    assert result.decision.action == "refer_to_underwriter"


async def test_enrichment_agent_uses_the_same_lookups():
    results = await enrichment_agent(make_submission(), FakeHazard(score=50))
    assert {r.source for r in results} == {
        "hazard_apify",
        "loss_history_mock",
        "property_data_mock",
    }
    memo = await risk_assessment_agent(make_submission(), results)
    assert "HZ-001" not in {f.rule_id for f in memo.flags}


async def test_incomplete_submission_skips_enrichment_entirely():
    hazard = FakeHazard()
    result = await run_pipeline(load_sample("incomplete"), "SUB-INC2", hazard=hazard)
    assert result.status == "needs_more_info"
    assert hazard.calls == 0
