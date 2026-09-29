"""MOCK property-characteristics lookup. A real version would call a property data provider."""

from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.utils import utcnow

SOURCE = "property_data_mock"


async def property_data_lookup(submission: Submission) -> EnrichmentResult:
    roof_age = utcnow().year - submission.roof_year if submission.roof_year else None
    return EnrichmentResult(
        source=SOURCE,
        data={
            "year_built": submission.year_built,
            "roof_year": submission.roof_year,
            "roof_age_years": roof_age,
        },
        retrieved_at=utcnow(),
    )
