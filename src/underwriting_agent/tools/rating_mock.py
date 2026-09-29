"""MOCK rating engine. Real premiums come from the existing deterministic rating system."""

from underwriting_agent.schemas import Submission


async def rating_quote(submission: Submission) -> float:
    """Placeholder premium: 0.4% of Coverage A. NOT a real rate."""
    return round(submission.coverage_a_limit * 0.004, 2)
