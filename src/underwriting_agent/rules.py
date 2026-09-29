"""Underwriting guideline thresholds and version stamps.

Phase 1 keeps these as constants. Later phases can load them from a versioned file so every
decision records exactly which rules were applied.
"""

RULES_VERSION = "2026-09-phase3"  # phase3: HZ-002 added; HZ-001 may use live FEMA NRI data
MODEL_VERSION = "rule-based-stub"  # replaced by the real model name once an LLM is wired in

ROOF_HARD_STOP_YEARS = 25  # HS-001: roof older than this is an automatic decline
CLAIMS_CAUTION_COUNT = 2  # SR-011: this many claims in five years needs review
# HZ-001: composite hazard score at or above this needs review. With live data the score is
# FEMA's NRI national percentile, so 70 refers roughly the riskiest 30% of US census tracts.
HAZARD_CAUTION_SCORE = 70
# HZ-002: the live hazard lookup failed, so hazard exposure needs manual review.
