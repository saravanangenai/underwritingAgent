"""Hazard lookup against FEMA's National Risk Index (NRI), through the Apify MCP server.

1. Geocode the property address with the US Census geocoder (keyless). The match must be
   in the same ZIP code, because the geocoder silently snaps to a nearby street otherwise.
2. Call the Apify Actor tool with the coordinates. This starts one Actor run (billed by Apify).
3. Read the run's dataset row with the `get-dataset-items` tool.

The NRI composite risk score (a 0-100 national percentile) is returned as `composite_score`,
the same field the mock returns, so the risk rules read either source. Every failure raises
HazardLookupError; the enrichment agent turns that into an "unavailable" result.

Smoke test (one billed run): uv run python -m underwriting_agent.tools.hazard_apify
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from underwriting_agent.config import Settings
from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.utils import utcnow

SOURCE = "hazard_apify"

# Only the fields we use, so the dataset read stays small.
_FIELDS = ",".join(
    [
        "match_status",
        "nri_lookup_status",
        "resolution_used",
        "county",
        "state_abbrev",
        "tract_fips",
        "nri_version",
        "risk_score",
        "risk_rating",
        "risk_percentile",
        "expected_annual_loss_rating",
        "top_hazards",
    ]
)


class HazardLookupError(Exception):
    """The live hazard lookup failed. Messages never include the Apify token."""


@dataclass
class GeoPoint:
    lat: float
    lon: float
    matched_address: str


class CensusGeocoder:
    """US Census Bureau one-line address geocoder. Free and needs no key."""

    URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

    def __init__(self, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None):
        self._timeout = timeout
        self._transport = transport  # tests pass a MockTransport

    async def locate(self, address: str, zip_code: str | None) -> GeoPoint:
        params = {"address": address, "benchmark": "Public_AR_Current", "format": "json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                response = await c.get(self.URL, params=params)
            response.raise_for_status()
            matches = response.json()["result"]["addressMatches"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise HazardLookupError(f"geocoder failed: {type(exc).__name__}") from exc

        if not matches:
            raise HazardLookupError("geocoder found no match for the property address")
        best = matches[0]
        matched_zip = best.get("addressComponents", {}).get("zip")
        if zip_code and matched_zip != zip_code:
            raise HazardLookupError(
                f"geocoder matched ZIP {matched_zip}, not the submitted {zip_code}"
            )
        coords = best["coordinates"]
        return GeoPoint(lat=coords["y"], lon=coords["x"], matched_address=best["matchedAddress"])


def _payload(result: Any) -> dict[str, Any]:
    """The JSON object from an MCP tool result (structured content, or JSON text content)."""
    if result.is_error:
        text = " ".join(getattr(c, "text", "") or "" for c in result.content)
        raise HazardLookupError(f"MCP tool error: {text[:200]}")
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        structured = structured["result"]  # MCP wraps non-object tool output as {"result": ...}
        if isinstance(structured, str):
            try:
                structured = json.loads(structured)
            except ValueError:
                structured = None
    if isinstance(structured, dict):
        return structured
    for content in result.content:
        text = getattr(content, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise HazardLookupError("MCP tool returned no JSON object")


def _leaf(exc: BaseException) -> BaseException:
    """The first real exception inside (possibly nested) ExceptionGroups.

    The MCP client runs in an anyio task group, so an error raised inside the session,
    including our own HazardLookupError, comes out wrapped in an ExceptionGroup.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


ConnectFn = Callable[[], AbstractAsyncContextManager[Client]]


class ApifyHazardProvider:
    def __init__(
        self,
        token: str,
        mcp_url: str = "https://mcp.apify.com",
        actor: str = "malonestar/fema-nri-county-risk-profile",
        resolution: str = "tract",
        timeout: float = 120.0,
        connect: ConnectFn | None = None,  # tests pass an in-process fake MCP server
        geocoder: CensusGeocoder | None = None,
    ):
        self._token = token
        self._timeout = timeout
        self._actor = actor
        self._tool = actor.replace("/", "--")  # how the Apify MCP server names Actor tools
        self._resolution = resolution
        sep = "&" if "?" in mcp_url else "?"
        self._url = f"{mcp_url}{sep}tools={actor}"
        self._connect = connect or self._remote_session
        self._geocoder = geocoder or CensusGeocoder()

    @asynccontextmanager
    async def _remote_session(self) -> AsyncIterator[Client]:
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx2.AsyncClient(headers=headers, timeout=self._timeout) as http:
            async with Client(streamable_http_client(self._url, http_client=http)) as client:
                yield client

    async def lookup(self, submission: Submission) -> EnrichmentResult:
        point = await self._geocoder.locate(submission.property_address, submission.zip_code)
        asset = {"lat": point.lat, "lon": point.lon, "label": submission.submission_id}
        actor_input = {"assets": [asset], "resolution": self._resolution, "maxResults": 1}
        try:
            async with asyncio.timeout(self._timeout), self._connect() as client:
                run = _payload(await client.call_tool(self._tool, actor_input))
                if run.get("status") != "SUCCEEDED":
                    raise HazardLookupError(f"Actor run ended with status {run.get('status')}")
                dataset_id = run["storages"]["datasets"]["default"]["id"]
                rows = _payload(
                    await client.call_tool(
                        "get-dataset-items",
                        {"datasetId": dataset_id, "limit": 1, "fields": _FIELDS},
                    )
                ).get("items")
        except Exception as exc:  # timeouts, transport errors, unexpected payload shapes
            leaf = _leaf(exc)
            if isinstance(leaf, HazardLookupError):
                raise leaf from None
            raise HazardLookupError(f"Apify MCP call failed: {type(leaf).__name__}") from exc

        row = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(row, dict):
            raise HazardLookupError("Actor run returned no rows")
        if row.get("match_status") != "matched" or row.get("nri_lookup_status") != "ok":
            raise HazardLookupError(
                f"NRI lookup {row.get('match_status')}/{row.get('nri_lookup_status')}"
            )
        score = row.get("risk_score")
        if not isinstance(score, int | float):
            raise HazardLookupError("NRI row has no risk_score")

        return EnrichmentResult(
            source=SOURCE,
            data={
                "status": "ok",
                "composite_score": score,
                "tier": row.get("risk_rating"),
                "expected_annual_loss_rating": row.get("expected_annual_loss_rating"),
                "top_hazards": row.get("top_hazards") or [],
                "resolution": row.get("resolution_used"),
                "tract_fips": row.get("tract_fips"),
                "county": row.get("county"),
                "state": row.get("state_abbrev"),
                "nri_version": row.get("nri_version"),
                "geocoded_address": point.matched_address,
                "apify_actor": self._actor,
                "apify_run_id": run.get("runId"),
            },
            retrieved_at=utcnow(),
        )


def build_hazard_provider(settings: Settings) -> ApifyHazardProvider | None:
    """The live provider when enabled and a token is set; None means use the mock."""
    if settings.hazard_provider != "apify" or settings.apify_token is None:
        return None
    token = settings.apify_token.get_secret_value()
    if not token:
        return None
    return ApifyHazardProvider(
        token=token,
        mcp_url=settings.apify_mcp_url,
        actor=settings.apify_hazard_actor,
        resolution=settings.apify_hazard_resolution,
        timeout=settings.apify_timeout_seconds,
    )


async def _main() -> int:
    provider = build_hazard_provider(Settings())
    if provider is None:
        print("Apify hazard lookups are off: set APIFY_TOKEN (and HAZARD_PROVIDER=apify) in .env.")
        return 1
    sample = Submission(
        submission_id="SMOKE-1",
        property_address="22 Willow Creek Dr, Rancho Santa Margarita, CA 92688",
        zip_code="92688",
        coverage_a_limit=1,
        requested_effective_date="2026-11-01",
    )
    try:
        result = await provider.lookup(sample)
    except HazardLookupError as exc:
        print(f"FAILED {exc}")
        return 1
    print(json.dumps(result.data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
