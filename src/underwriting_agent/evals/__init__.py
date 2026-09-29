"""Labelled evaluation set: runs synthetic submissions through the pipeline and scores them.

Each case in cases.json has the decision a careful underwriter would make. "rules" cases are
decided by the deterministic rules; "judgment" cases need judgment the rules do not encode,
which is where the LLM risk review should add value (some judgment cases expect approval, to
catch over-referral). Hazard data is fixed per case, so runs never call Apify.

Year and date fields may be templates resolved at load time, so labels stay true as time
passes: "{year-24}" is 24 years before the current year and "{date+60}" is 60 days from today.

    uv run python -m underwriting_agent.evals                   # rules only (free)
    uv run python -m underwriting_agent.evals --llm             # rules + LLM review
    uv run python -m underwriting_agent.evals --llm --repeat 3  # also measure consistency
    uv run python -m underwriting_agent.evals --llm --save      # scorecard to eval-results/
    uv run python -m underwriting_agent.evals --gate            # exit 1 on a regression (CI)
"""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from underwriting_agent.graph.orchestrator import run_pipeline
from underwriting_agent.llm import LLM
from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.tools import hazard_apify
from underwriting_agent.utils import utcnow

CASES_FILE = Path(__file__).parent / "cases.json"
_TEMPLATE = re.compile(r"^\{(year|date)([+-]\d+)?\}$")


def _resolve(value: Any) -> Any:
    """Resolve "{year-24}" / "{date+60}" templates relative to today (UTC)."""
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    if isinstance(value, str) and (m := _TEMPLATE.match(value)):
        offset = int(m.group(2) or 0)
        today = utcnow().date()
        if m.group(1) == "year":
            return today.year + offset
        return (today + timedelta(days=offset)).isoformat()
    return value


def load_cases() -> list[dict[str, Any]]:
    return _resolve(json.loads(CASES_FILE.read_text(encoding="utf-8")))


class StaticHazard:
    """Returns a case's fixed hazard data as if it came from the live FEMA lookup."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    async def lookup(self, submission: Submission) -> EnrichmentResult:
        data = self.data if "status" in self.data else {"status": "ok", **self.data}
        return EnrichmentResult(source=hazard_apify.SOURCE, data=data, retrieved_at=utcnow())


@dataclass
class CaseResult:
    id: str
    kind: str
    expected: str
    actual: str
    flags: list[str]
    assessed_by: str
    confidence: float | None
    rejected_flags: list[str]

    @property
    def correct(self) -> bool:
        return self.expected == self.actual

    @property
    def unsafe(self) -> bool:
        """Approved something a careful underwriter would refer or decline."""
        return self.actual == "auto_approve" and self.expected != "auto_approve"


async def run_case(case: dict[str, Any], llm: LLM | None = None) -> CaseResult:
    result = await run_pipeline(
        case["submission"],
        f"EVAL-{case['id']}",
        hazard=StaticHazard(case["hazard"]),
        risk_llm=llm,
    )
    memo = result.risk_memo
    return CaseResult(
        id=case["id"],
        kind=case["kind"],
        expected=case["expected"],
        actual=result.decision.action if result.decision else result.status,
        flags=[f.rule_id for f in memo.flags if f.severity != "info"] if memo else [],
        assessed_by=memo.assessed_by if memo else "-",
        confidence=result.decision.confidence if result.decision else None,
        rejected_flags=memo.rejected_flags if memo else [],
    )


async def run_eval(
    llm: LLM | None = None, cases: list[dict[str, Any]] | None = None
) -> list[CaseResult]:
    # Sequential on purpose: keeps LLM rate limits happy and the output in order.
    return [await run_case(case, llm) for case in (cases or load_cases())]


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    def share(rs: list[CaseResult]) -> str:
        return f"{sum(r.correct for r in rs)}/{len(rs)}"

    return {
        "accuracy": share(results),
        "rules_cases": share([r for r in results if r.kind == "rules"]),
        "judgment_cases": share([r for r in results if r.kind == "judgment"]),
        "unsafe_approvals": sum(r.unsafe for r in results),
        "over_referrals": sum(
            r.expected == "auto_approve" and r.actual == "refer_to_underwriter" for r in results
        ),
        "llm_fallbacks": sum(r.assessed_by == "rules" for r in results),
        "rejected_llm_flags": sum(len(r.rejected_flags) for r in results),
    }


def consistency(runs: list[list[CaseResult]]) -> dict[str, Any]:
    """How often each case got the same decision across repeated runs."""
    by_case: dict[str, list[str]] = {}
    for run in runs:
        for r in run:
            by_case.setdefault(r.id, []).append(r.actual)
    unstable = {
        case_id: dict(Counter(actions))
        for case_id, actions in by_case.items()
        if len(set(actions)) > 1
    }
    return {
        "runs": len(runs),
        "stable_cases": f"{len(by_case) - len(unstable)}/{len(by_case)}",
        "unstable": unstable,
    }


def gate_failures(results: list[CaseResult], llm_used: bool) -> list[str]:
    """Results that should fail the gate.

    Rules only (CI): any wrong rules case is a regression, since the rules are deterministic.
    Judgment misses are the known gap, not a regression.

    With an LLM: any unsafe approval, and any case that should have been declined but was
    not. Over-referrals (an approve case sent for review) are extra caution, not a safety
    failure; `summarize` and `consistency` report them instead.
    """
    if not llm_used:
        return [
            f"{r.id}: expected {r.expected}, got {r.actual}"
            for r in results
            if r.kind == "rules" and not r.correct
        ]
    failures = [f"{r.id}: unsafe approval" for r in results if r.unsafe]
    failures += [
        f"{r.id}: expected auto_decline, got {r.actual}"
        for r in results
        if r.expected == "auto_decline" and r.actual not in {"auto_decline", "auto_approve"}
    ]
    return failures


def save_scorecard(
    runs: list[list[CaseResult]], meta: dict[str, Any], out_dir: Path = Path("eval-results")
) -> Path:
    """Write one JSON scorecard, so runs can be compared over time."""
    from underwriting_agent import rules
    from underwriting_agent.agents.risk_assessment import RISK_PROMPT_VERSION

    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}-{meta.get('mode', 'rules')}.json"
    card = {
        **meta,
        "created_at": utcnow().isoformat() + "Z",
        "rules_version": rules.RULES_VERSION,
        "risk_prompt_version": RISK_PROMPT_VERSION,
        "summary": [summarize(run) for run in runs],
        "consistency": consistency(runs),
        "cases": [[asdict(r) for r in run] for run in runs],
    }
    path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return path
