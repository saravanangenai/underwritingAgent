"""Underwriting guideline thresholds and version stamps.

Phase 1 keeps these as constants. Later phases can load them from a versioned file so every
decision records exactly which rules were applied.
"""

RULES_VERSION = "2026-09-phase1"
MODEL_VERSION = "rule-based-stub"  # replaced by the real model name once an LLM is wired in

ROOF_HARD_STOP_YEARS = 25  # HS-001: roof older than this is an automatic decline
CLAIMS_CAUTION_COUNT = 2  # SR-011: this many claims in five years needs review
HAZARD_CAUTION_SCORE = 70  # HZ-001: composite hazard score at or above this needs review
