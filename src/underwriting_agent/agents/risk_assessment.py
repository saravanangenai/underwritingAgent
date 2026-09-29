"""Risk assessment agent.

PHASE 1 STUB: flags come from fixed rules in code. Phase 4 replaces this with an LLM that
reasons over the same inputs and must still cite a rule id and source field for every flag.
"""

from underwriting_agent import rules
from underwriting_agent.schemas import EnrichmentResult, RiskFlag, RiskMemo, Submission
from underwriting_agent.tools import (
    hazard_apify,
    hazard_mock,
    loss_history_mock,
    property_data_mock,
)


async def risk_assessment_agent(
    submission: Submission, enrichment: list[EnrichmentResult]
) -> RiskMemo:
    data = {r.source: r.data for r in enrichment}
    # Live FEMA data when present, otherwise the mock. Flags cite whichever was used.
    hazard_source = next(
        (s for s in (hazard_apify.SOURCE, hazard_mock.SOURCE) if s in data), hazard_mock.SOURCE
    )
    hazard = data.get(hazard_source, {})
    losses = data.get(loss_history_mock.SOURCE, {})
    prop = data.get(property_data_mock.SOURCE, {})

    flags: list[RiskFlag] = []

    roof_age = prop.get("roof_age_years")
    roof_field = f"{property_data_mock.SOURCE}.roof_age_years"
    if roof_age is not None and roof_age > rules.ROOF_HARD_STOP_YEARS:
        flags.append(
            RiskFlag(
                rule_id="HS-001",
                issue=f"Roof is {roof_age} years old (limit {rules.ROOF_HARD_STOP_YEARS})",
                severity="hard_stop",
                source_field=roof_field,
            )
        )
    elif roof_age is not None:
        flags.append(
            RiskFlag(
                rule_id="INFO-001",
                issue=f"Roof installed {prop.get('roof_year')} ({roof_age} years old)",
                severity="info",
                source_field=roof_field,
            )
        )

    if submission.has_pool:
        flags.append(
            RiskFlag(
                rule_id="SR-010",
                issue="Pool present: confirm fence and self-latching gate",
                severity="caution",
                source_field="submission.has_pool",
            )
        )

    claims = losses.get("claims_last_5_years", 0)
    if claims >= rules.CLAIMS_CAUTION_COUNT:
        flags.append(
            RiskFlag(
                rule_id="SR-011",
                issue=f"{claims} claims in the last 5 years",
                severity="caution",
                source_field=f"{loss_history_mock.SOURCE}.claims_last_5_years",
            )
        )

    if submission.has_trampoline:
        flags.append(
            RiskFlag(
                rule_id="SR-012",
                issue="Trampoline present: liability exposure",
                severity="caution",
                source_field="submission.has_trampoline",
            )
        )

    hazard_score = hazard.get("composite_score")
    if hazard.get("status") == "unavailable":
        flags.append(
            RiskFlag(
                rule_id="HZ-002",
                issue=f"Hazard data unavailable ({hazard.get('error')}): review hazard manually",
                severity="caution",
                source_field=f"{hazard_source}.status",
            )
        )
    elif hazard_score is not None and hazard_score >= rules.HAZARD_CAUTION_SCORE:
        flags.append(
            RiskFlag(
                rule_id="HZ-001",
                issue=f"High composite hazard score ({hazard_score})",
                severity="caution",
                source_field=f"{hazard_source}.composite_score",
            )
        )

    hard = sum(f.severity == "hard_stop" for f in flags)
    caution = sum(f.severity == "caution" for f in flags)
    narrative = (
        f"{hard} hard-stop and {caution} caution flag(s) found (rules {rules.RULES_VERSION})."
    )
    return RiskMemo(flags=flags, narrative=narrative)
