"""Underwriter review: a person's decision on a submission the pipeline referred.

The review is appended to the hash-chained decision log as a new entry. It carries forward the
inputs and risk memo of the decision it resolves, so the latest entry for a submission is
always self-contained, and it names the entry it supersedes. The automated entry stays in the
chain unchanged.
"""

from datetime import datetime

from underwriting_agent.schemas import (
    DecisionLogEntry,
    ReviewRequest,
    TriageDecision,
    UnderwriterReview,
)

# review action -> (decision action recorded, submission status afterwards)
REVIEW_OUTCOMES = {
    "approve": ("underwriter_approve", "completed"),
    "decline": ("underwriter_decline", "declined"),
    "request_info": ("underwriter_request_info", "needs_more_info"),
}

REVIEWABLE_STATUSES = {"referred"}


def build_review_entry(
    previous: DecisionLogEntry, request: ReviewRequest, reviewed_at: datetime, reviewer: str
) -> DecisionLogEntry:
    """`reviewer` is the signed-in user, recorded as 'Display Name (username)'."""
    action, _ = REVIEW_OUTCOMES[request.action]
    return DecisionLogEntry(
        submission_id=previous.submission_id,
        decision=TriageDecision(
            action=action,
            confidence=None,  # a person's decision, not a model's
            rationale=f"Underwriter {reviewer}: {request.note}",
            # A quote only makes sense for an approval.
            estimated_premium=(
                previous.decision.estimated_premium if request.action == "approve" else None
            ),
        ),
        risk_memo=previous.risk_memo,
        model_version=previous.model_version,
        rules_version=previous.rules_version,
        created_at=reviewed_at,
        inputs=previous.inputs,
        prompt_versions=previous.prompt_versions,
        llm_review_raw=None,  # stays on the automated entry this review supersedes
        review=UnderwriterReview(
            action=request.action,
            note=request.note,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            supersedes_entry_hash=previous.entry_hash,
        ),
    )
