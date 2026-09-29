"""MOCK hazard lookup. Phase 3 replaces this with the Apify hazard/peril MCP call."""

from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.utils import utcnow

SOURCE = "hazard_mock"

# Fake composite hazard scores (0-100) for a few demo ZIP codes. Not real risk data.
_SCORE_BY_ZIP = {"92688": 22, "93001": 78}
_DEFAULT_SCORE = 35


async def hazard_lookup(submission: Submission) -> EnrichmentResult:
    score = _SCORE_BY_ZIP.get(submission.zip_code or "", _DEFAULT_SCORE)
    tier = "high" if score >= 70 else "moderate" if score >= 40 else "low"
    return EnrichmentResult(
        source=SOURCE,
        data={"composite_score": score, "tier": tier, "note": "mock data, not real hazard scores"},
        retrieved_at=utcnow(),
    )
