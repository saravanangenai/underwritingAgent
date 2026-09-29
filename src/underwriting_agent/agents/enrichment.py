"""Enrichment agent: the data lookups for one submission.

The LangGraph pipeline (graph/builder.py) fans these out as parallel `Send` tasks, one per
name from `select_lookups`. `enrichment_agent` runs the same lookups with asyncio.gather for
direct use and tests.
"""

import asyncio
from typing import Protocol

import structlog

from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.tools import hazard_apify
from underwriting_agent.tools.hazard_apify import HazardLookupError
from underwriting_agent.tools.hazard_mock import hazard_lookup as mock_hazard_lookup
from underwriting_agent.tools.loss_history_mock import loss_history_lookup
from underwriting_agent.tools.property_data_mock import property_data_lookup
from underwriting_agent.utils import utcnow

log = structlog.get_logger()

LOOKUP_NAMES = ("hazard", "loss_history", "property_data")


class HazardProvider(Protocol):
    async def lookup(self, submission: Submission) -> EnrichmentResult: ...


def select_lookups(submission: Submission) -> list[str]:
    """The lookups to run for this submission.

    Every submission needs all three today. This is the hook for per-submission choices,
    such as a coastal wind lookup only when coastal_distance_miles is small.
    """
    return list(LOOKUP_NAMES)


async def hazard_lookup(
    submission: Submission, provider: HazardProvider | None = None
) -> EnrichmentResult:
    """Live hazard data when a provider is configured, otherwise the mock.

    A failed live lookup is reported as unavailable rather than replaced with mock scores,
    so no decision is ever made on fake hazard data. The risk agent flags it (HZ-002).
    """
    if provider is None:
        return await mock_hazard_lookup(submission)
    try:
        return await provider.lookup(submission)
    except HazardLookupError as exc:
        log.warning(
            "hazard_lookup_unavailable", submission_id=submission.submission_id, error=str(exc)
        )
        return EnrichmentResult(
            source=hazard_apify.SOURCE,
            data={"status": "unavailable", "error": str(exc)},
            retrieved_at=utcnow(),
        )


async def run_lookup(
    name: str, submission: Submission, hazard: HazardProvider | None = None
) -> EnrichmentResult:
    if name == "hazard":
        return await hazard_lookup(submission, hazard)
    if name == "loss_history":
        return await loss_history_lookup(submission)
    if name == "property_data":
        return await property_data_lookup(submission)
    raise ValueError(f"Unknown lookup {name!r}")


async def enrichment_agent(
    submission: Submission, hazard: HazardProvider | None = None
) -> list[EnrichmentResult]:
    names = select_lookups(submission)
    return list(await asyncio.gather(*(run_lookup(n, submission, hazard) for n in names)))
