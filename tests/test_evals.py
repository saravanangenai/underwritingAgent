"""Phase 5: the evaluation set, its templates, the CI gate, consistency and scorecards."""

import json

import pytest

from tests.fakes import FakeLLM, risk_review
from underwriting_agent.evals import (
    CaseResult,
    _resolve,
    consistency,
    gate_failures,
    load_cases,
    run_eval,
    save_scorecard,
    summarize,
)
from underwriting_agent.utils import utcnow


def test_cases_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 38
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["kind"] in {"rules", "judgment"}
        assert case["expected"] in {"auto_approve", "refer_to_underwriter", "auto_decline"}
        assert case["why"]
        # Every template was resolved.
        text = json.dumps(case["submission"])
        assert "{year" not in text and "{date" not in text


def test_templates_resolve_relative_to_today():
    today = utcnow().date()
    assert _resolve("{year}") == today.year
    assert _resolve("{year-24}") == today.year - 24
    assert _resolve({"a": ["{year+5}"]}) == {"a": [today.year + 5]}
    assert _resolve("{date+0}") == today.isoformat()
    assert _resolve("2025-06-01") == "2025-06-01"  # fixed values stay fixed
    assert _resolve("{nope}") == "{nope}"


async def test_rules_only_baseline():
    summary = summarize(await run_eval())
    # The rules decide every rules case and approve every judgment case.
    assert summary["rules_cases"] == "24/24"
    assert summary["judgment_cases"] == "4/14"
    assert summary["unsafe_approvals"] == 10
    assert summary["over_referrals"] == 0


async def test_a_perfect_reviewer_scores_everything():
    refer = {c["id"] for c in load_cases() if c["expected"] == "refer_to_underwriter"}

    class Scripted(FakeLLM):
        async def complete_json(self, system, user):
            case_id = json.loads(user)["submission"]["submission_id"].removeprefix("EVAL-")
            self.data = risk_review("refer" if case_id in refer else "approve", 0.9)
            return await super().complete_json(system, user)

    summary = summarize(await run_eval(Scripted()))
    assert summary["accuracy"] == "38/38"
    assert summary["unsafe_approvals"] == 0
    assert summary["over_referrals"] == 0


async def test_an_over_cautious_reviewer_shows_up_as_over_referrals():
    summary = summarize(await run_eval(FakeLLM(risk_review("refer", 0.9))))
    assert summary["unsafe_approvals"] == 0
    assert summary["over_referrals"] == 14  # every case that should be approved


def _result(case_id, expected, actual, kind="rules") -> CaseResult:
    return CaseResult(case_id, kind, expected, actual, [], "rules", None, [])


def test_gate_fails_on_a_wrong_rules_case_but_not_the_known_judgment_gap():
    ok = _result("A1", "auto_approve", "auto_approve")
    judgment_gap = _result("J1", "refer_to_underwriter", "auto_approve", kind="judgment")
    assert gate_failures([ok, judgment_gap], llm_used=False) == []

    broken = _result("D1", "auto_decline", "auto_approve")
    assert gate_failures([ok, broken], llm_used=False) == [
        "D1: expected auto_decline, got auto_approve"
    ]


def test_gate_with_an_llm_fails_on_any_unsafe_approval():
    judgment_gap = _result("J1", "refer_to_underwriter", "auto_approve", kind="judgment")
    assert gate_failures([judgment_gap], llm_used=True) == ["J1: unsafe approval"]


def test_gate_with_an_llm_fails_when_a_decline_is_missed():
    missed = _result("D1", "auto_decline", "refer_to_underwriter")
    assert gate_failures([missed], llm_used=True) == [
        "D1: expected auto_decline, got refer_to_underwriter"
    ]


def test_gate_with_an_llm_tolerates_over_referral():
    over_cautious = _result("A3", "auto_approve", "refer_to_underwriter")
    assert gate_failures([over_cautious], llm_used=True) == []
    # Without an LLM the same result is a rules regression.
    assert gate_failures([over_cautious], llm_used=False) != []


def test_consistency_reports_unstable_cases():
    run1 = [
        _result("A1", "auto_approve", "auto_approve"),
        _result("K3", "auto_approve", "auto_approve"),
    ]
    run2 = [
        _result("A1", "auto_approve", "auto_approve"),
        _result("K3", "auto_approve", "refer_to_underwriter"),
    ]
    stats = consistency([run1, run2])
    assert stats["stable_cases"] == "1/2"
    assert stats["unstable"] == {"K3": {"auto_approve": 1, "refer_to_underwriter": 1}}


@pytest.mark.parametrize("mode", ["rules", "llm"])
def test_scorecard_is_saved(tmp_path, mode):
    runs = [[_result("A1", "auto_approve", "auto_approve")]]
    path = save_scorecard(runs, {"mode": mode, "models": []}, out_dir=tmp_path)
    card = json.loads(path.read_text())
    assert path.name.endswith(f"-{mode}.json")
    assert card["summary"][0]["accuracy"] == "1/1"
    assert card["rules_version"] and card["risk_prompt_version"]
    assert card["cases"][0][0]["id"] == "A1"
