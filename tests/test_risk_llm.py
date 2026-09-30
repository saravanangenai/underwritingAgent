"""Phase 4: the LLM risk review and the triage guardrails around it."""

import pytest

from tests.fakes import ACORD_EXTRACTION, FakeLLM, RoutingLLM, risk_review
from tests.test_agents import make_submission
from underwriting_agent import rules
from underwriting_agent.agents.compliance import compliance_agent
from underwriting_agent.agents.enrichment import enrichment_agent
from underwriting_agent.agents.risk_assessment import risk_assessment_agent
from underwriting_agent.agents.triage import triage_agent
from underwriting_agent.graph.orchestrator import run_pipeline
from underwriting_agent.samples import load_sample


async def review(llm, **overrides):
    sub = make_submission(**overrides)
    memo = await risk_assessment_agent(sub, await enrichment_agent(sub), llm)
    return memo, await triage_agent(sub, memo)


# --- flag validation ---


async def test_valid_llm_flag_is_accepted_with_catalog_severity():
    flag = {
        "rule_id": "UW-105",
        "issue": "Roof 2001, house 2010",
        "source_field": "submission.roof_year",
    }
    memo, decision = await review(FakeLLM(risk_review("refer", 0.8, [flag])))
    llm_flags = [f for f in memo.flags if f.origin == "llm"]
    assert [(f.rule_id, f.severity, f.source_field) for f in llm_flags] == [
        ("UW-105", "caution", "submission.roof_year")
    ]
    assert memo.rejected_flags == []
    assert decision.action == "refer_to_underwriter"


@pytest.mark.parametrize(
    ("flag", "reason"),
    [
        ({"rule_id": "UW-999", "source_field": "submission.roof_year"}, "unknown rule id"),
        ({"rule_id": "HS-001", "source_field": "submission.roof_year"}, "evaluated by code"),
        ({"rule_id": "SR-010", "source_field": "submission.has_pool"}, "evaluated by code"),
        ({"rule_id": "UW-101", "source_field": "submission.square_feet"}, "not in the input"),
        ({"rule_id": "UW-103", "source_field": "made_up_source.score"}, "not in the input"),
    ],
)
async def test_invalid_llm_flags_are_rejected_and_recorded(flag, reason):
    llm = FakeLLM(risk_review("approve", 0.95, [{"issue": "x", **flag}]))
    memo, decision = await review(llm)
    assert all(f.origin == "rule" for f in memo.flags)
    assert len(memo.rejected_flags) == 1 and reason in memo.rejected_flags[0]
    assert decision.action == "auto_approve"  # a rejected flag does not count


async def test_llm_cannot_raise_a_hard_stop():
    # UW rules are cautions in the catalog, so a claimed hard stop is downgraded.
    flag = {
        "rule_id": "UW-104",
        "issue": "coast",
        "source_field": "submission.coastal_distance_miles",
    }
    memo, decision = await review(
        FakeLLM(risk_review("decline", 1.0, [{**flag, "severity": "hard_stop"}])),
        coastal_distance_miles=0.2,
    )
    assert [f.severity for f in memo.flags if f.origin == "llm"] == ["caution"]
    assert decision.action == "refer_to_underwriter"


async def test_nested_source_field_is_normalized():
    flag = {
        "rule_id": "UW-103",
        "issue": "Wildfire very high",
        "source_field": "hazard_mock.tier",
    }
    memo, _ = await review(FakeLLM(risk_review("refer", 0.7, [flag])))
    assert memo.flags[-1].source_field == "hazard_mock.tier"

    flag["source_field"] = "hazard_mock.note[0].x"
    memo, _ = await review(FakeLLM(risk_review("refer", 0.7, [flag])))
    assert memo.flags[-1].source_field == "hazard_mock.note"


# --- triage guardrails ---


async def test_confident_llm_approval_approves_with_confidence():
    memo, decision = await review(FakeLLM(risk_review("approve", 0.92)))
    assert decision.action == "auto_approve"
    assert decision.confidence == 0.92
    assert memo.assessed_by == "fake/fake-model"
    assert memo.composite_risk_score == 30


async def test_low_confidence_approval_is_referred():
    _, decision = await review(
        FakeLLM(risk_review("approve", rules.LLM_APPROVE_MIN_CONFIDENCE - 0.01))
    )
    assert decision.action == "refer_to_underwriter"
    assert "below" in decision.rationale


async def test_llm_refer_is_followed():
    _, decision = await review(FakeLLM(risk_review("refer", 0.6)))
    assert decision.action == "refer_to_underwriter"
    assert decision.confidence == 0.6


async def test_llm_decline_becomes_refer():
    _, decision = await review(FakeLLM(risk_review("decline", 0.99)))
    assert decision.action == "refer_to_underwriter"
    assert "only hard-stop rules decline" in decision.rationale


async def test_llm_cannot_approve_over_a_rule_caution():
    memo, decision = await review(FakeLLM(risk_review("approve", 1.0)), has_pool=True)
    assert "SR-010" in {f.rule_id for f in memo.flags}
    assert decision.action == "refer_to_underwriter"


async def test_llm_cannot_approve_over_a_hard_stop():
    _, decision = await review(FakeLLM(risk_review("approve", 1.0)), roof_year=1990)
    assert decision.action == "auto_decline"
    assert decision.confidence == 1.0


# --- failure handling ---


@pytest.mark.parametrize(
    "llm",
    [
        FakeLLM(error="All LLM providers failed: groq: HTTP 500"),
        FakeLLM({"flags": "not a list", "recommendation": "maybe"}),
        FakeLLM(risk_review("approve", 1.7)),  # confidence out of range
    ],
)
async def test_llm_failure_falls_back_to_rules(llm):
    memo, decision = await review(llm)
    assert memo.assessed_by == "rules"
    assert memo.recommendation is None
    assert "rules only" in memo.narrative
    assert decision.action == "auto_approve"  # same as the rules-only result


# --- audit trail and pipeline ---


async def test_audit_records_the_risk_model():
    sub = make_submission()
    memo, decision = await review(FakeLLM(risk_review()))
    entry = await compliance_agent(sub, memo, decision)
    assert entry.model_version == "risk=fake/fake-model"

    entry = await compliance_agent(sub, memo, decision, intake_model="groq/x")
    assert entry.model_version == "intake=groq/x; risk=fake/fake-model"


async def test_pipeline_passes_the_risk_llm_through():
    llm = FakeLLM(risk_review("approve", 0.9))
    result = await run_pipeline(load_sample("clean"), "SUB-P4", risk_llm=llm)
    assert result.risk_memo.assessed_by == "fake/fake-model"
    assert result.decision.confidence == 0.9
    # The review sees the rules' flags and today's date.
    assert '"rule_flags_already_raised"' in llm.calls[0][1]
    assert '"today"' in llm.calls[0][1]


def test_api_uses_llm_risk_review_when_enabled(settings, users):
    from fastapi.testclient import TestClient

    from tests.conftest import sign_in
    from underwriting_agent.api.main import create_app

    settings.llm_risk_assessment = True
    llm = RoutingLLM(intake=ACORD_EXTRACTION, risk=risk_review("refer", 0.8))
    with TestClient(create_app(settings, llm=llm)) as client:
        sign_in(client)
        body = client.post("/underwrite", json=load_sample("acord_text")).json()
        logged = client.get(f"/submissions/{body['submission_id']}/decision").json()
    assert body["risk_memo"]["assessed_by"] == "fake/fake-model"
    assert body["risk_memo"]["recommendation"]["action"] == "refer"
    assert logged["model_version"] == "intake=fake/fake-model; risk=fake/fake-model"
    assert len(llm.calls) == 2  # one intake call, one risk review call
