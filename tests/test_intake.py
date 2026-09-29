import pytest

from tests.fakes import ACORD_EXTRACTION, FakeLLM
from underwriting_agent.agents.intake import intake_agent
from underwriting_agent.llm import LLMError
from underwriting_agent.samples import load_sample


async def test_valid_payload_becomes_submission():
    result = await intake_agent(load_sample("clean"), "SUB-1")
    assert result.missing_fields == []
    sub = result.submission
    assert sub is not None
    assert sub.property_address == "22 Willow Creek Dr, Rancho Santa Margarita, CA 92688"
    assert sub.coverage_a_limit == 650000
    assert sub.has_pool is False


async def test_missing_coverage_limit_is_reported():
    result = await intake_agent(load_sample("incomplete"), "SUB-2")
    assert result.submission is None
    assert "coverage.coverage_a_limit is required" in result.missing_fields


async def test_missing_address_part_is_reported():
    raw = load_sample("clean")
    del raw["property"]["zip"]
    result = await intake_agent(raw, "SUB-3")
    assert result.submission is None
    assert any("property address" in m for m in result.missing_fields)


async def test_non_object_sections_are_treated_as_missing():
    result = await intake_agent({"property": "nope", "coverage": 5}, "SUB-4")
    assert result.submission is None
    assert len(result.missing_fields) == 3


async def test_invalid_limit_is_reported_with_its_field():
    raw = load_sample("clean")
    raw["coverage"]["coverage_a_limit"] = "abc"
    result = await intake_agent(raw, "SUB-5")
    assert result.submission is None
    assert any("coverage.coverage_a_limit" in m for m in result.missing_fields)


async def test_acord_text_is_extracted_by_the_llm():
    llm = FakeLLM(ACORD_EXTRACTION)
    result = await intake_agent(load_sample("acord_text"), "SUB-6", llm)
    assert result.missing_fields == []
    assert result.model_id == "fake/fake-model"
    sub = result.submission
    assert sub.property_address == "415 Harbor View Ln, San Clemente, CA 92672"
    assert sub.coverage_a_limit == 720000
    assert sub.has_pool is True
    assert str(sub.requested_effective_date) == "2026-12-01"
    # The raw application text is sent as the user message, not mixed into the prompt.
    assert llm.calls[0][1] == load_sample("acord_text")["acord_text"]


async def test_acord_text_missing_fields_are_reported():
    extraction = {**ACORD_EXTRACTION, "coverage": {"coverage_a_limit": None}}
    result = await intake_agent(load_sample("acord_text"), "SUB-7", FakeLLM(extraction))
    assert result.submission is None
    assert "coverage.coverage_a_limit is required" in result.missing_fields


async def test_acord_text_without_llm_raises():
    with pytest.raises(LLMError, match="needs an LLM provider"):
        await intake_agent(load_sample("acord_text"), "SUB-8")
