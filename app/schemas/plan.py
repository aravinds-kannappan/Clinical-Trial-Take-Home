"""The QueryPlan: the single intermediate representation every question compiles to.

A planner (LLM or rule-based) produces a QueryPlan; one executor runs it. The plan is
deliberately *closed*: every choice is an enum or a bounded value so that an LLM cannot
invent fields, metrics, or data. Semantic constraints that JSON schema cannot express are
enforced in ``validate_plan``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import (
    AnalysisKind,
    Dimension,
    EntityType,
    Measure,
    NumericField,
    OverallStatus,
    Phase,
    SortOrder,
    SponsorClass,
    StudyType,
    TimeField,
    TimeGranularity,
)


class CohortFilters(BaseModel):
    """Filters that select one set of trials. Each maps to a ClinicalTrials.gov parameter."""

    model_config = ConfigDict(extra="forbid")

    intervention: str | None = Field(default=None, description="Drug/intervention name (query.intr)")
    condition: str | None = Field(default=None, description="Condition/disease (query.cond)")
    sponsor: str | None = Field(default=None, description="Sponsor or collaborator name (query.spons)")
    country: str | None = Field(default=None, description="Country or location text (query.locn)")
    free_text: str | None = Field(default=None, description="Fallback full-text term (query.term)")
    statuses: list[OverallStatus] | None = Field(default=None, description="Overall status filter")
    phases: list[Phase] | None = Field(default=None, description="Phase filter")
    study_type: StudyType | None = None
    sponsor_class: SponsorClass | None = None
    start_year_from: int | None = Field(default=None, ge=1900, le=2100)
    start_year_to: int | None = Field(default=None, ge=1900, le=2100)

    @field_validator("intervention", "condition", "sponsor", "country", "free_text")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    def is_empty(self) -> bool:
        return all(v is None for v in self.model_dump().values())

    def describe(self) -> str:
        """Short human phrase used in titles, e.g. 'pembrolizumab in breast cancer'."""
        parts: list[str] = []
        if self.intervention:
            parts.append(self.intervention)
        if self.condition:
            parts.append(("in " if parts else "") + self.condition)
        if self.free_text and not parts:
            parts.append(self.free_text)
        if self.sponsor:
            parts.append(f"sponsored by {self.sponsor}")
        if self.country:
            parts.append(f"in {self.country}")
        return " ".join(parts)


class Cohort(BaseModel):
    """A labelled set of trials. Comparisons run several cohorts side by side."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Series label shown in the legend")
    filters: CohortFilters


class NetworkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_types: list[EntityType] = Field(
        description="One type -> co-occurrence network among that type; two types -> bipartite network",
        min_length=1,
        max_length=2,
    )
    min_edge_weight: int = Field(default=1, ge=1, le=1000)
    max_nodes: int = Field(default=40, ge=5, le=200)


class ScatterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: NumericField
    y: NumericField
    color: Dimension | None = Field(default=None, description="Optional categorical colour channel")


class HistogramSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: NumericField
    bins: int = Field(default=10, ge=3, le=50)


class QueryPlan(BaseModel):
    """What to fetch, how to aggregate it, and how to present it."""

    model_config = ConfigDict(extra="forbid")

    analysis: AnalysisKind
    cohorts: list[Cohort] = Field(min_length=1, max_length=4)
    dimension: Dimension | None = Field(
        default=None, description="Group-by field for distribution/comparison; ignored otherwise"
    )
    measure: Measure = Measure.TRIAL_COUNT
    time_field: TimeField = TimeField.START_DATE
    granularity: TimeGranularity = TimeGranularity.YEAR
    network: NetworkSpec | None = None
    scatter: ScatterSpec | None = None
    histogram: HistogramSpec | None = None
    top_n: int | None = Field(default=None, ge=1, le=100)
    sort: SortOrder = SortOrder.NATURAL
    interpretation: str = Field(
        default="", description="One sentence explaining how the question was interpreted"
    )


class PlanValidationError(ValueError):
    """Raised when a plan is structurally valid JSON but semantically impossible to run."""


def validate_plan(plan: QueryPlan) -> QueryPlan:
    """Enforce cross-field constraints and fill deterministic defaults. Returns the plan."""
    kind = plan.analysis
    n = len(plan.cohorts)

    for cohort in plan.cohorts:
        f = cohort.filters
        if f.start_year_from and f.start_year_to and f.start_year_from > f.start_year_to:
            raise PlanValidationError(
                f"Cohort '{cohort.label}': start_year_from ({f.start_year_from}) is after "
                f"start_year_to ({f.start_year_to})."
            )

    if kind == AnalysisKind.COMPARISON:
        if n < 2:
            raise PlanValidationError("A comparison needs at least two cohorts.")
        if plan.dimension is None:
            plan.dimension = Dimension.PHASE
        labels = [c.label for c in plan.cohorts]
        if len(set(labels)) != len(labels):
            raise PlanValidationError("Cohort labels must be unique.")
    elif n != 1:
        raise PlanValidationError(f"Analysis '{kind.value}' expects exactly one cohort, got {n}.")

    if kind == AnalysisKind.DISTRIBUTION and plan.dimension is None:
        raise PlanValidationError("A distribution needs a dimension to group by.")

    if kind == AnalysisKind.GEOGRAPHIC:
        plan.dimension = Dimension.COUNTRY

    if kind == AnalysisKind.RELATIONSHIP:
        if plan.network is None:
            plan.network = NetworkSpec(node_types=[EntityType.SPONSOR, EntityType.INTERVENTION])
        if len(set(plan.network.node_types)) != len(plan.network.node_types):
            # [drug, drug] means a co-occurrence network: collapse to one type.
            plan.network.node_types = list(dict.fromkeys(plan.network.node_types))

    if kind == AnalysisKind.SCATTER:
        if plan.scatter is None:
            plan.scatter = ScatterSpec(x=NumericField.START_YEAR, y=NumericField.ENROLLMENT)
        if plan.scatter.x == plan.scatter.y:
            raise PlanValidationError("Scatter x and y must be different fields.")

    if kind == AnalysisKind.HISTOGRAM and plan.histogram is None:
        plan.histogram = HistogramSpec(field=NumericField.ENROLLMENT)

    if kind in (AnalysisKind.DISTRIBUTION, AnalysisKind.COMPARISON) and plan.dimension == Dimension.START_YEAR:
        # Grouping by start year is a time trend in disguise; keep it but use natural order.
        plan.sort = SortOrder.NATURAL

    return plan
