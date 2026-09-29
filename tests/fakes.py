from typing import Any

import httpx
from mcp.server import MCPServer

from underwriting_agent.llm import LLMError, LLMReply
from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.tools.hazard_apify import HazardLookupError
from underwriting_agent.utils import utcnow

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


# --- Hazard (Phase 3) ---

NRI_ROW: dict[str, Any] = {
    "match_status": "matched",
    "nri_lookup_status": "ok",
    "resolution_used": "tract",
    "county": "Orange",
    "state_abbrev": "CA",
    "tract_fips": "06059032053",
    "nri_version": "December 2025",
    "risk_score": 89.96,
    "risk_rating": "Relatively High",
    "expected_annual_loss_rating": "Relatively High",
    "top_hazards": [{"code": "IFLD", "hazard": "Inland Flooding", "score": 95.8}],
}


def fake_apify_server(
    rows: list[dict[str, Any]] | None = None, run_status: str = "SUCCEEDED"
) -> tuple[MCPServer, list[tuple[str, dict[str, Any]]]]:
    """An in-process MCP server shaped like Apify's, plus the list of calls it received."""
    server = MCPServer("fake-apify")
    calls: list[tuple[str, dict[str, Any]]] = []

    @server.tool(name="owner--actor", structured_output=True)
    def run_actor(
        assets: list[dict], resolution: str = "county", maxResults: int = 1
    ) -> dict[str, Any]:
        calls.append(("owner--actor", {"assets": assets, "resolution": resolution}))
        return {
            "runId": "run-1",
            "status": run_status,
            "storages": {"datasets": {"default": {"id": "ds-1"}}},
        }

    @server.tool(name="get-dataset-items", structured_output=True)
    def get_items(datasetId: str, limit: int = 20, fields: str = "") -> dict[str, Any]:
        calls.append(("get-dataset-items", {"datasetId": datasetId, "fields": fields}))
        return {"items": [NRI_ROW] if rows is None else rows}

    return server, calls


def census_transport(zip_code: str = "92618", matches: bool = True) -> httpx.MockTransport:
    """A Census geocoder that answers every address with one match in `zip_code`."""

    def handler(request: httpx.Request) -> httpx.Response:
        found = [
            {
                "matchedAddress": "1 TEST ST, IRVINE, CA, " + zip_code,
                "coordinates": {"x": -117.8, "y": 33.68},
                "addressComponents": {"zip": zip_code},
            }
        ]
        return httpx.Response(200, json={"result": {"addressMatches": found if matches else []}})

    return httpx.MockTransport(handler)


class FakeHazard:
    """A hazard provider that returns a fixed live-style score, or fails."""

    def __init__(self, score: float = 90.0, error: str | None = None):
        self.score = score
        self.error = error
        self.calls = 0

    async def lookup(self, submission: Submission) -> EnrichmentResult:
        self.calls += 1
        if self.error:
            raise HazardLookupError(self.error)
        return EnrichmentResult(
            source="hazard_apify",
            data={"status": "ok", "composite_score": self.score},
            retrieved_at=utcnow(),
        )


# --- Risk review (Phase 4) ---


def risk_review(
    recommendation: str = "approve",
    confidence: float = 0.9,
    flags: list[dict[str, str]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A well-formed LLM risk review reply."""
    return {
        "flags": flags or [],
        "narrative": "Synthetic memo.",
        "risk_score": 30,
        "recommendation": recommendation,
        "confidence": confidence,
        "rationale": "Synthetic rationale.",
        **extra,
    }


class RoutingLLM(FakeLLM):
    """Answers the ACORD intake prompt and the risk review prompt with different replies."""

    def __init__(self, intake: dict[str, Any], risk: dict[str, Any]):
        super().__init__()
        self.intake, self.risk = intake, risk

    async def complete_json(self, system: str, user: str) -> LLMReply:
        self.calls.append((system, user))
        data = self.risk if "underwriting assistant" in system else self.intake
        return LLMReply(data=data, provider="fake", model="fake-model")
