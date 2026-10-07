"""Request schema for POST /v1/visualize.

Structured fields are optional and always override whatever the planner infers from the
free-text query. This gives callers a deterministic way to pin entities.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import OverallStatus, Phase, PlannerChoice, TimeGranularity


class VisualizeRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "query": "How has the number of trials for this drug changed over time since 2015?",
                    "drug_name": "Pembrolizumab",
                }
            ]
        },
    )

    query: str = Field(min_length=3, max_length=1000, description="Natural-language question")

    # Optional structured fields (candidate-defined)
    drug_name: str | None = Field(default=None, max_length=200, description="Intervention/drug name")
    condition: str | None = Field(default=None, max_length=200, description="Condition or disease")
    sponsor: str | None = Field(default=None, max_length=200, description="Sponsor/collaborator name")
    country: str | None = Field(default=None, max_length=100, description="Country name")
    trial_phase: Phase | None = Field(default=None, description="Restrict to one phase label")
    status: OverallStatus | None = Field(default=None, description="Restrict to one overall status")
    start_year: int | None = Field(default=None, ge=1900, le=2100, description="Earliest start year")
    end_year: int | None = Field(default=None, ge=1900, le=2100, description="Latest start year")

    # Execution knobs
    max_trials: int | None = Field(
        default=None, ge=50, le=5000, description="Cap on trials fetched per cohort (default 2000)"
    )
    top_n: int | None = Field(default=None, ge=1, le=100, description="Keep only the top N categories")
    time_granularity: TimeGranularity | None = Field(default=None, description="Override bucket size")
    max_citations_per_datum: int | None = Field(
        default=None, ge=0, le=200, description="Citations kept per datum (default 25; 0 disables)"
    )
    planner: PlannerChoice = Field(
        default=PlannerChoice.AUTO,
        description="auto = LLM when configured else rules; llm = require LLM; rules = deterministic only",
    )

    @model_validator(mode="after")
    def _check_years(self) -> "VisualizeRequest":
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be <= end_year")
        return self
