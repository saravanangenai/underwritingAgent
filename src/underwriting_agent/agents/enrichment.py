"""Enrichment agent: runs every data lookup concurrently and collects the results.

Phase 3 turns this fan-out into LangGraph `Send` tasks chosen per submission.
"""

import asyncio

from underwriting_agent.schemas import EnrichmentResult, Submission
from underwriting_agent.tools.hazard_mock import hazard_lookup
from underwriting_agent.tools.loss_history_mock import loss_history_lookup
from underwriting_agent.tools.property_data_mock import property_data_lookup


async def enrichment_agent(submission: Submission) -> list[EnrichmentResult]:
    results = await asyncio.gather(
        hazard_lookup(submission),
        loss_history_lookup(submission),
        property_data_lookup(submission),
    )
    return list(results)
