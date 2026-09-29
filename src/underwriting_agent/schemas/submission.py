from datetime import date

from pydantic import BaseModel, Field


class Submission(BaseModel):
    """A validated homeowners submission, as produced by the Intake agent."""

    submission_id: str
    property_address: str
    zip_code: str | None = None
    year_built: int | None = None
    roof_year: int | None = None
    coverage_a_limit: float = Field(gt=0)
    requested_effective_date: date
    has_pool: bool = False
    has_trampoline: bool = False
    prior_carrier: str | None = None
    claims_last_5_years: int = Field(default=0, ge=0)
    coastal_distance_miles: float | None = None
