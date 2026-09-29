from typing import Any

from underwriting_agent.llm import LLMError, LLMReply

# What a model should extract from samples/acord_text.json.
ACORD_EXTRACTION: dict[str, Any] = {
    "applicant": {"first_name": "Sample", "last_name": "Applicant"},
    "property": {
        "address_line1": "415 Harbor View Ln",
        "city": "San Clemente",
        "state": "CA",
        "zip": "92672",
        "year_built": 2008,
        "square_feet": 2650,
        "construction_type": "Masonry",
        "roof_type": "Tile",
        "roof_year": 2019,
        "has_pool": True,
        "has_trampoline": False,
        "coastal_distance_miles": 3.5,
    },
    "coverage": {
        "coverage_a_limit": 720000,
        "deductible": 2500,
        "requested_effective_date": "2026-12-01",
    },
    "prior_insurance": {
        "carrier": "Example Mutual",
        "years_with_carrier": 6,
        "claims_last_5_years": 0,
    },
}


class FakeLLM:
    """Returns a canned reply (or raises) and records the prompts it was given."""

    def __init__(self, data: dict[str, Any] | None = None, error: str | None = None):
        self.data = data or {}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system: str, user: str) -> LLMReply:
        self.calls.append((system, user))
        if self.error:
            raise LLMError(self.error)
        return LLMReply(data=self.data, provider="fake", model="fake-model")
