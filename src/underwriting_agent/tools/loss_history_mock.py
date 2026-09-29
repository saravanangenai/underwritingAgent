"""MOCK loss-history lookup. A real version would call a claims-history data vendor."""

from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.utils import utcnow

SOURCE = "loss_history_mock"


async def loss_history_lookup(submission: Submission) -> EnrichmentResult:
    return EnrichmentResult(
        source=SOURCE,
        data={"claims_last_5_years": submission.claims_last_5_years},
        retrieved_at=utcnow(),
    )
