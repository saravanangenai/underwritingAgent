import pytest

from tests.fakes import ACORD_EXTRACTION, ACORD_EXTRACTION_WITH_EVIDENCE, FakeLLM
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


def _with_evidence(**changes):
    """The evidence-backed extraction with some evidence entries replaced or removed."""
    evidence = dict(ACORD_EXTRACTION_WITH_EVIDENCE["evidence"])
    for path, entry in changes.items():
        path = path.replace("__", ".")
        if entry is None:
            evidence.pop(path, None)
        else:
            evidence[path] = entry
    return {**ACORD_EXTRACTION_WITH_EVIDENCE, "evidence": evidence}


async def test_acord_values_with_matching_quotes_are_verified():
    llm = FakeLLM(ACORD_EXTRACTION_WITH_EVIDENCE)
    result = await intake_agent(load_sample("acord_text"), "SUB-E1", llm)
    assert result.missing_fields == []
    ev = result.evidence["coverage.coverage_a_limit"]
    assert (ev["status"], ev["value"], ev["quote"]) == ("verified", 720000, "A - DWELLING $720,000")
    assert all(e["status"] == "verified" for e in result.evidence.values())


async def test_quote_is_matched_ignoring_case_and_spacing():
    quote = {"quote": "a  -  dwelling   $720,000", "confidence": 0.9}
    extraction = _with_evidence(coverage__coverage_a_limit=quote)
    result = await intake_agent(load_sample("acord_text"), "SUB-E2", FakeLLM(extraction))
    assert result.evidence["coverage.coverage_a_limit"]["status"] == "verified"


async def test_a_value_whose_quote_is_not_in_the_text_is_dropped():
    # The model "read" $900,000 and quoted words the application does not contain.
    extraction = _with_evidence(
        coverage__coverage_a_limit={"quote": "DWELLING $900,000", "confidence": 0.99}
    )
    extraction["coverage"] = {**extraction["coverage"], "coverage_a_limit": 900000}
    result = await intake_agent(load_sample("acord_text"), "SUB-E3", FakeLLM(extraction))
    assert result.submission is None
    assert result.evidence["coverage.coverage_a_limit"]["status"] == "quote_not_found"
    assert any(
        "coverage.coverage_a_limit could not be verified" in m and "not in the application" in m
        for m in result.missing_fields
    )


async def test_a_low_confidence_required_field_needs_more_info():
    extraction = _with_evidence(
        coverage__coverage_a_limit={"quote": "A - DWELLING $720,000", "confidence": 0.3}
    )
    result = await intake_agent(load_sample("acord_text"), "SUB-E4", FakeLLM(extraction))
    assert result.submission is None
    assert any("low confidence (30%)" in m for m in result.missing_fields)


async def test_a_low_confidence_optional_field_is_left_unknown():
    extraction = _with_evidence(
        property__roof_year={"quote": "Tile, replaced 2019", "confidence": 0.2}
    )
    result = await intake_agent(load_sample("acord_text"), "SUB-E5", FakeLLM(extraction))
    assert result.missing_fields == []
    assert result.submission.roof_year is None
    assert result.evidence["property.roof_year"]["status"] == "low_confidence"


async def test_a_value_without_evidence_is_kept_but_marked():
    extraction = _with_evidence(property__year_built=None)
    result = await intake_agent(load_sample("acord_text"), "SUB-E6", FakeLLM(extraction))
    assert result.submission.year_built == 2008
    assert result.evidence["property.year_built"]["status"] == "no_evidence"


async def test_json_submissions_have_no_intake_evidence():
    result = await intake_agent(load_sample("clean"), "SUB-E7")
    assert result.evidence == {}


async def test_acord_text_without_llm_raises():
    with pytest.raises(LLMError, match="needs an LLM provider"):
        await intake_agent(load_sample("acord_text"), "SUB-8")
