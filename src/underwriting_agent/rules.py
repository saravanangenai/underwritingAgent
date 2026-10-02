"""Underwriting guideline thresholds, the rule catalog and version stamps.

These are constants for now. Later phases can load them from a versioned file so every
decision records exactly which rules were applied.
"""

from typing import Literal

# phase3: HZ-002 added; HZ-001 may use live FEMA NRI data.
# phase4: judgment rules UW-101..UW-105, raised only by the LLM.
# phase5: UW-101 bounded to Coverage A below $100k or above $5M (or clear contradictions).
RULES_VERSION = "2026-09-phase5"
MODEL_VERSION = "rule-based"  # what the audit log records when no LLM assessed the risk

ROOF_HARD_STOP_YEARS = 25  # HS-001: roof older than this is an automatic decline
CLAIMS_CAUTION_COUNT = 2  # SR-011: this many claims in five years needs review
# HZ-001: composite hazard score at or above this needs review. With live data the score is
# FEMA's NRI national percentile, so 70 refers roughly the riskiest 30% of US census tracts.
HAZARD_CAUTION_SCORE = 70
# HZ-002: the live hazard lookup failed, so hazard exposure needs manual review.

# UW-101: Coverage A outside this range is implausible for a homeowners dwelling. Inside it,
# the LLM may raise UW-101 only when the limit clearly contradicts other submission data.
COVERAGE_A_PLAUSIBLE_MIN = 100_000
COVERAGE_A_PLAUSIBLE_MAX = 5_000_000

# The LLM may approve only when no rule fired and it is at least this confident.
LLM_APPROVE_MIN_CONFIDENCE = 0.7
# Intake: a value read from ACORD text with less confidence than this is dropped, so a required
# field falls back to "needs more info" and an optional one stays unknown.
INTAKE_MIN_CONFIDENCE = 0.6

Severity = Literal["info", "caution", "hard_stop"]


class Rule:
    def __init__(
        self,
        severity: Severity,
        description: str,
        llm_may_raise: bool = False,
        cites: tuple[str, ...] | None = None,
    ):
        self.severity = severity
        self.description = description
        self.llm_may_raise = llm_may_raise
        # When set, an LLM flag for this rule must cite one of these fields. This stops the
        # model raising it from a guess (for example the city) when the real field is empty.
        self.cites = cites


# Every rule id a flag may cite. Deterministic rules are evaluated in code; judgment rules
# (llm_may_raise=True) can only be raised by the LLM risk assessment, which must cite a field.
CATALOG: dict[str, Rule] = {
    "HS-001": Rule("hard_stop", f"Roof older than {ROOF_HARD_STOP_YEARS} years"),
    "INFO-001": Rule("info", "Roof age noted"),
    "SR-010": Rule("caution", "Pool present: confirm fence and self-latching gate"),
    "SR-011": Rule("caution", f"{CLAIMS_CAUTION_COUNT}+ claims in the last 5 years"),
    "SR-012": Rule("caution", "Trampoline present: liability exposure"),
    "HZ-001": Rule("caution", f"Composite hazard score {HAZARD_CAUTION_SCORE} or higher"),
    "HZ-002": Rule("caution", "Hazard data unavailable: review hazard exposure manually"),
    "UW-101": Rule(
        "caution",
        f"Coverage A is below ${COVERAGE_A_PLAUSIBLE_MIN:,} or above "
        f"${COVERAGE_A_PLAUSIBLE_MAX:,}, or clearly contradicts other submission data "
        "(possible under- or over-insurance). Missing square footage or construction "
        "details are not a reason to raise it",
        llm_may_raise=True,
    ),
    "UW-102": Rule(
        "caution",
        "Requested effective date is in the past or more than 12 months away",
        llm_may_raise=True,
        cites=("submission.requested_effective_date",),
    ),
    "UW-103": Rule(
        "caution",
        "A single peril is rated Very High even though the composite hazard score is below "
        "the HZ-001 cutoff",
        llm_may_raise=True,
    ),
    "UW-104": Rule(
        "caution",
        "coastal_distance_miles is given and is 1 mile or less. Do not guess from the city or "
        "address when it is missing",
        llm_may_raise=True,
        cites=("submission.coastal_distance_miles",),
    ),
    "UW-105": Rule(
        "caution",
        "Submission data is internally inconsistent (for example the roof is older than the "
        "house, or a year is in the future)",
        llm_may_raise=True,
    ),
}
