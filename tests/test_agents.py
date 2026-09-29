from datetime import date

from underwriting_agent.agents.enrichment import enrichment_agent
from underwriting_agent.agents.risk_assessment import risk_assessment_agent
from underwriting_agent.agents.triage import triage_agent
from underwriting_agent.schemas import Submission


def make_submission(**overrides) -> Submission:
    fields = {
        "submission_id": "SUB-T",
        "property_address": "1 Test St, Irvine, CA 92618",
        "zip_code": "92618",
        "roof_year": 2020,
        "coverage_a_limit": 500000,
        "requested_effective_date": date(2026, 11, 1),
    }
    fields.update(overrides)
    return Submission(**fields)


async def assess(**overrides):
    sub = make_submission(**overrides)
    memo = await risk_assessment_agent(sub, await enrichment_agent(sub))
    return sub, memo


async def test_enrichment_returns_all_three_sources():
    results = await enrichment_agent(make_submission())
    assert {r.source for r in results} == {"hazard_mock", "loss_history_mock", "property_data_mock"}


async def test_old_roof_is_a_hard_stop_and_declines():
    sub, memo = await assess(roof_year=1990)
    assert [f.rule_id for f in memo.flags if f.severity == "hard_stop"] == ["HS-001"]
    decision = await triage_agent(sub, memo)
    assert decision.action == "auto_decline"
    assert decision.confidence == 1.0
    assert decision.estimated_premium is None


async def test_pool_and_claims_are_cautions_and_refer():
    sub, memo = await assess(has_pool=True, claims_last_5_years=2)
    cautions = {f.rule_id for f in memo.flags if f.severity == "caution"}
    assert cautions == {"SR-010", "SR-011"}
    assert (await triage_agent(sub, memo)).action == "refer_to_underwriter"


async def test_clean_risk_auto_approves_with_mock_premium():
    sub, memo = await assess()
    decision = await triage_agent(sub, memo)
    assert decision.action == "auto_approve"
    assert decision.estimated_premium == 2000.0  # 0.4% of 500,000 (mock rate)


async def test_every_flag_cites_a_source_field():
    _, memo = await assess(has_pool=True, has_trampoline=True, claims_last_5_years=3)
    assert memo.flags
    assert all(f.rule_id and f.source_field for f in memo.flags)
