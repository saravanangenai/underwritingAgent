from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EnrichmentResult(BaseModel):
    """One lookup result. `source` names the tool that produced it."""

    source: str
    data: dict[str, Any]
    retrieved_at: datetime
