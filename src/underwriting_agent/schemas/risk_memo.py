from typing import Literal

from pydantic import BaseModel


class RiskFlag(BaseModel):
    rule_id: str  # which guideline was applied, e.g. "SR-010"
    issue: str
    severity: Literal["info", "caution", "hard_stop"]
    source_field: str  # citation: the data field that triggered the flag


class RiskMemo(BaseModel):
    flags: list[RiskFlag]
    narrative: str
    composite_risk_score: float | None = None
