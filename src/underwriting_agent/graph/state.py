from dataclasses import dataclass, field

from underwriting_agent.schemas import (
    DecisionLogEntry,
    EnrichmentResult,
    RiskMemo,
    Submission,
    TriageDecision,
)

# Maps a triage action to the status stored on the submission.
STATUS_FOR_ACTION = {
    "auto_approve": "completed",
    "auto_decline": "declined",
    "refer_to_underwriter": "referred",
}


@dataclass
class PipelineResult:
    submission_id: str
    status: str
    missing_fields: list[str] = field(default_factory=list)
    submission: Submission | None = None
    enrichment: list[EnrichmentResult] = field(default_factory=list)
    risk_memo: RiskMemo | None = None
    decision: TriageDecision | None = None
    log_entry: DecisionLogEntry | None = None
