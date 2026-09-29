import pytest

from tests.fakes import ACORD_EXTRACTION, FakeLLM
from tests.test_agents import make_submission
from underwriting_agent import rules
from underwriting_agent.agents.compliance import compliance_agent
from underwriting_agent.agents.risk_assessment import risk_assessment_agent
from underwriting_agent.agents.triage import triage_agent
from underwriting_agent.graph.orchestrator import run_pipeline
from underwriting_agent.llm import LLMError
from underwriting_agent.samples import load_sample


async def _decide(sub):
    memo = await risk_assessment_agent(sub, [])
    return memo, await triage_agent(sub, memo)


async def test_compliance_stamps_rule_and_model_versions():
    sub = make_submission()
    memo, decision = await _decide(sub)
    entry = await compliance_agent(sub, memo, decision)
    assert entry.submission_id == "SUB-T"
    assert entry.rules_version == rules.RULES_VERSION
    assert entry.model_version == rules.MODEL_VERSION
    assert entry.created_at.tzinfo is None  # naive UTC for SQLite


async def test_compliance_records_the_intake_model():
    sub = make_submission()
    memo, decision = await _decide(sub)
    entry = await compliance_agent(sub, memo, decision, intake_model="groq/test-model")
    assert entry.model_version == f"intake=groq/test-model; risk={rules.MODEL_VERSION}"


@pytest.mark.parametrize(
    ("sample", "status", "action"),
    [
        ("clean", "completed", "auto_approve"),
        ("high_risk", "referred", "refer_to_underwriter"),
        ("decline", "declined", "auto_decline"),
    ],
)
async def test_pipeline_outcome_for_each_json_sample(sample, status, action):
    result = await run_pipeline(load_sample(sample), f"SUB-{sample}")
    assert result.status == status
    assert result.decision.action == action
    assert result.log_entry.decision == result.decision
    assert len(result.enrichment) == 3


async def test_pipeline_stops_after_intake_when_fields_are_missing():
    result = await run_pipeline(load_sample("incomplete"), "SUB-INC")
    assert result.status == "needs_more_info"
    assert result.missing_fields
    assert result.enrichment == []
    assert result.decision is None and result.log_entry is None


async def test_json_submission_never_calls_the_llm():
    llm = FakeLLM(ACORD_EXTRACTION)
    await run_pipeline(load_sample("clean"), "SUB-NOLLM", llm)
    assert llm.calls == []


async def test_pipeline_runs_acord_text_through_the_llm():
    result = await run_pipeline(load_sample("acord_text"), "SUB-ACORD", FakeLLM(ACORD_EXTRACTION))
    assert result.status == "referred"
    assert result.submission.coverage_a_limit == 720000
    assert result.log_entry.model_version.startswith("intake=fake/fake-model")


async def test_pipeline_propagates_llm_failure():
    with pytest.raises(LLMError, match="All providers down"):
        await run_pipeline(
            load_sample("acord_text"), "SUB-DOWN", FakeLLM(error="All providers down")
        )
