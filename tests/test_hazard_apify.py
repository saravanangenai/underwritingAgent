import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import Client

from tests.fakes import NRI_ROW, census_transport, fake_apify_server
from tests.test_agents import make_submission
from underwriting_agent.config import Settings
from underwriting_agent.tools.hazard_apify import (
    ApifyHazardProvider,
    CensusGeocoder,
    HazardLookupError,
    build_hazard_provider,
)


def provider_for(server, geocoder_transport=None) -> ApifyHazardProvider:
    return ApifyHazardProvider(
        token="test-token",
        actor="owner/actor",
        connect=lambda: Client(server),
        geocoder=CensusGeocoder(transport=geocoder_transport or census_transport()),
    )


async def test_lookup_returns_nri_score_as_composite_score():
    server, calls = fake_apify_server()
    result = await provider_for(server).lookup(make_submission())

    assert result.source == "hazard_apify"
    assert result.data["status"] == "ok"
    assert result.data["composite_score"] == 89.96
    assert result.data["tier"] == "Relatively High"
    assert result.data["tract_fips"] == "06059032053"
    assert result.data["top_hazards"][0]["code"] == "IFLD"
    assert result.data["apify_run_id"] == "run-1"
    assert result.data["geocoded_address"].endswith("92618")


async def test_lookup_sends_geocoded_point_then_reads_the_run_dataset():
    server, calls = fake_apify_server()
    await provider_for(server).lookup(make_submission())

    (tool, args), (items_tool, items_args) = calls
    assert tool == "owner--actor"  # Apify MCP names Actor tools owner--name
    assert args["assets"] == [{"lat": 33.68, "lon": -117.8, "label": "SUB-T"}]
    assert args["resolution"] == "tract"
    assert items_tool == "get-dataset-items"
    assert items_args["datasetId"] == "ds-1"
    assert "risk_score" in items_args["fields"]


GEOCODE_FAILED = {**NRI_ROW, "match_status": "geocode_failed", "risk_score": None}
COUNTY_ROW = {**NRI_ROW, "resolution_used": "county", "tract_fips": None}


async def test_actor_geocode_failure_falls_back_to_county_fips():
    server, calls = fake_apify_server(rows_per_run=[[GEOCODE_FAILED], [COUNTY_ROW]])
    transport = census_transport(county_fips="06059")
    result = await provider_for(server, transport).lookup(make_submission())

    assert result.data["status"] == "ok"
    assert result.data["resolution"] == "county"
    assert result.data["fallback"] == "county_fips"
    first, _, second, _ = calls
    assert first[1]["assets"][0].keys() >= {"lat", "lon"}
    assert second[1]["assets"] == [{"fips": "06059", "label": "SUB-T"}]
    assert second[1]["resolution"] == "county"


async def test_actor_geocode_failure_without_a_county_still_raises():
    server, calls = fake_apify_server(rows=[GEOCODE_FAILED])
    with pytest.raises(HazardLookupError, match="geocode_failed"):
        await provider_for(server).lookup(make_submission())  # county endpoint answers 500
    assert len(calls) == 2  # one run, no retry


async def test_geocoder_sends_the_one_line_address():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return census_transport().handle_request(request)

    point = await CensusGeocoder(transport=httpx.MockTransport(handler)).locate(
        "1 Test St, Irvine, CA 92618", "92618"
    )
    assert seen["address"] == "1 Test St, Irvine, CA 92618"
    assert seen["format"] == "json"
    assert (point.lat, point.lon) == (33.68, -117.8)


@pytest.mark.parametrize(
    ("transport", "message"),
    [
        (census_transport(matches=False), "no match"),
        (census_transport(zip_code="90210"), "not the submitted 92618"),
        (httpx.MockTransport(lambda r: httpx.Response(500)), "geocoder failed"),
        (httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")), "geocoder failed"),
    ],
)
async def test_geocoder_problems_raise(transport, message):
    server, calls = fake_apify_server()
    with pytest.raises(HazardLookupError, match=message):
        await provider_for(server, transport).lookup(make_submission())
    assert calls == []  # nothing is billed when the address cannot be placed


@pytest.mark.parametrize(
    ("server_kwargs", "message"),
    [
        ({"run_status": "FAILED"}, "status FAILED"),
        ({"rows": []}, "no rows"),
        ({"rows": [{**NRI_ROW, "match_status": "not_found"}]}, "not_found"),
        ({"rows": [{**NRI_ROW, "nri_lookup_status": "error"}]}, "matched/error"),
        ({"rows": [{**NRI_ROW, "risk_score": None}]}, "no risk_score"),
    ],
)
async def test_bad_actor_results_raise(server_kwargs, message):
    server, _ = fake_apify_server(**server_kwargs)
    with pytest.raises(HazardLookupError, match=message):
        await provider_for(server).lookup(make_submission())


async def test_unknown_tool_is_an_error():
    server, _ = fake_apify_server()
    provider = ApifyHazardProvider(
        token="t",
        actor="someone/else",  # the fake server has no such tool
        connect=lambda: Client(server),
        geocoder=CensusGeocoder(transport=census_transport()),
    )
    with pytest.raises(HazardLookupError, match="MCP tool error"):
        await provider.lookup(make_submission())


async def test_connection_failure_raises_without_leaking_the_token():
    @asynccontextmanager
    async def broken():
        raise ConnectionError("could not reach mcp.apify.com with test-token")
        yield

    provider = ApifyHazardProvider(
        token="test-token", connect=broken, geocoder=CensusGeocoder(transport=census_transport())
    )
    with pytest.raises(HazardLookupError) as exc:
        await provider.lookup(make_submission())
    assert "test-token" not in str(exc.value)
    assert "ConnectionError" in str(exc.value)


async def test_text_only_tool_results_are_parsed():
    # Some servers return JSON as text content instead of structured content.
    from mcp.server import MCPServer

    server = MCPServer("text-apify")

    @server.tool(name="owner--actor")
    def run_actor(assets: list[dict], resolution: str = "county", maxResults: int = 1) -> str:
        return json.dumps(
            {
                "status": "SUCCEEDED",
                "runId": "r",
                "storages": {"datasets": {"default": {"id": "d"}}},
            }
        )

    @server.tool(name="get-dataset-items")
    def get_items(datasetId: str, limit: int = 20, fields: str = "") -> str:
        return json.dumps({"items": [NRI_ROW]})

    result = await provider_for(server).lookup(make_submission())
    assert result.data["composite_score"] == 89.96


def test_build_hazard_provider_needs_a_token_and_apify_mode():
    base = {"_env_file": None}
    assert build_hazard_provider(Settings(**base, apify_token=None)) is None
    assert build_hazard_provider(Settings(**base, apify_token="")) is None
    mock_mode = Settings(**base, apify_token="t", hazard_provider="mock")
    assert build_hazard_provider(mock_mode) is None
    assert isinstance(build_hazard_provider(Settings(**base, apify_token="t")), ApifyHazardProvider)
