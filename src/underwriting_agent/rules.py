"""Underwriting guideline thresholds, the rule catalog and version stamps.

These are constants for now. Later phases can load them from a versioned file so every
decision records exactly which rules were applied.
"""

from typing import Literal

# phase3: HZ-002 added; HZ-001 may use live FEMA NRI data.
# phase4: judgment rules UW-101..UW-105, raised only by the LLM.
RULES_VERSION = "2026-09-phase4"
MODEL_VERSION = "rule-based"  # what the audit log records when no LLM assessed the risk

ROOF_HARD_STOP_YEARS = 25  # HS-001: roof older than this is an automatic decline
CLAIMS_CAUTION_COUNT = 2  # SR-011: this many claims in five years needs review
# HZ-001: composite hazard score at or above this needs review. With live data the score is
# FEMA's NRI national percentile, so 70 refers roughly the riskiest 30% of US census tracts.
HAZARD_CAUTION_SCORE = 70
# HZ-002: the live hazard lookup failed, so hazard exposure needs manual review.

# The LLM may approve only when no rule fired and it is at least this confident.
LLM_APPROVE_MIN_CONFIDENCE = 0.7

Severity = Literal["info", "caution", "hard_stop"]


class Rule:
    def __init__(self, severity: Severity, description: str, llm_may_raise: bool = False):
        self.severity = severity
        self.description = description
        self.llm_may_raise = llm_may_raise


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
        "Coverage A looks implausible for the property (possible under- or over-insurance)",
        llm_may_raise=True,
    ),
    "UW-102": Rule(
        "caution",
        "Requested effective date is in the past or more than 12 months away",
        llm_may_raise=True,
    ),
    "UW-103": Rule(
        "caution",
        "A single peril is rated Very High even though the composite hazard score is below "
        "the HZ-001 cutoff",
        llm_may_raise=True,
    ),
    "UW-104": Rule("caution", "Property is within 1 mile of the coast", llm_may_raise=True),
    "UW-105": Rule(
        "caution",
        "Submission data is internally inconsistent (for example the roof is older than the "
        "house, or a year is in the future)",
        llm_may_raise=True,
    ),
}
