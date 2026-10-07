"""Response schema for POST /v1/visualize.

The ``visualization`` block is what a renderer consumes; ``meta`` explains how it was
produced. Row models are typed per visualization so the contract is unambiguous.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import AnalysisKind, VisualizationType


# --------------------------------------------------------------------------- citations
class Evidence(BaseModel):
    """One exact field/value pair from the ClinicalTrials.gov API response."""

    field: str = Field(description="JSON path in the API study record, e.g. protocolSection.designModule.phases[0]")
    excerpt: str = Field(description="Exact value found at that path")


class Citation(BaseModel):
    nct_id: str
    title: str = Field(description="Brief title of the study, for display")
    url: str = Field(description="Link to the study on clinicaltrials.gov")
    evidence: list[Evidence] = Field(description="Field/value pairs that place this trial in the datum")


class CitedDatum(BaseModel):
    """Mixin fields present on every datum."""

    citations: list[Citation] = Field(default_factory=list)
    citation_count: int = Field(description="Total trials behind this datum (may exceed len(citations))")
    citations_truncated: bool = False


# --------------------------------------------------------------------------- rows per viz type
class CategoryRow(CitedDatum):
    """bar_chart and grouped_bar_chart rows."""

    category: str
    series: str | None = Field(default=None, description="Cohort label (grouped_bar_chart only)")
    value: float


class TimeRow(CitedDatum):
    """time_series rows. One row per period per series, gaps filled with 0."""

    period: str = Field(description="Label such as '2019', '2019-Q2', '2019-06'")
    period_start: str = Field(description="ISO date of the first day of the period")
    series: str | None = None
    value: float


class GeoRow(CitedDatum):
    """choropleth_map rows (also renderable as a bar chart)."""

    country: str
    iso3: str | None = Field(description="ISO 3166-1 alpha-3, null if the name could not be resolved")
    iso_numeric: str | None = Field(description="ISO 3166-1 numeric id for TopoJSON joins")
    value: float


class NetworkNode(CitedDatum):
    id: str = Field(description="Stable id '<type>:<normalized name>'")
    label: str
    type: str = Field(description="Entity type: sponsor, intervention, condition, country, ...")
    weight: float = Field(description="Number of trials mentioning this entity")


class NetworkEdge(CitedDatum):
    source: str = Field(description="Node id")
    target: str = Field(description="Node id")
    weight: float = Field(description="Number of trials in which both entities co-occur")


class NetworkData(BaseModel):
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]


class ScatterPoint(CitedDatum):
    nct_id: str
    label: str
    x: float
    y: float
    color: str | None = None


class HistogramBin(CitedDatum):
    bin_start: float
    bin_end: float
    bin_label: str
    count: int


DataRows = list[CategoryRow] | list[TimeRow] | list[GeoRow] | list[ScatterPoint] | list[HistogramBin] | NetworkData


# --------------------------------------------------------------------------- encoding
class Channel(BaseModel):
    """One visual channel: which data field it reads and how to label/scale it."""

    model_config = ConfigDict(extra="allow")

    field: str
    type: Literal["nominal", "ordinal", "quantitative", "temporal"]
    title: str
    unit: str | None = None


class VisualizationSpec(BaseModel):
    type: VisualizationType
    title: str
    subtitle: str | None = None
    encoding: dict[str, Any] = Field(description="Channel map; shape depends on `type` (see README)")
    data: DataRows
    config: dict[str, Any] = Field(default_factory=dict, description="Rendering hints: sort, units, scale, ...")


# --------------------------------------------------------------------------- meta
class CohortSummary(BaseModel):
    label: str
    filters_applied: dict[str, Any]
    total_matched: int = Field(description="Total studies matching on ClinicalTrials.gov")
    trials_analyzed: int = Field(description="Studies actually fetched and aggregated")
    truncated: bool = Field(description="True if trials_analyzed < total_matched because of max_trials")
    api_requests: list[str]


class ResponseMeta(BaseModel):
    intent: AnalysisKind
    planner_used: Literal["llm", "rules"]
    llm_model: str | None = None
    plan: dict[str, Any] = Field(description="The exact QueryPlan that was executed")
    cohorts: list[CohortSummary]
    trials_analyzed: int
    notes: list[str] = Field(default_factory=list, description="Interpretation choices and assumptions")
    warnings: list[str] = Field(default_factory=list, description="Data-quality or fallback warnings")
    source: str = "clinicaltrials.gov"
    source_api: str = "https://clinicaltrials.gov/api/v2/studies"
    generated_at: datetime
    timing_ms: dict[str, int] = Field(default_factory=dict)


class VisualizeResponse(BaseModel):
    visualization: VisualizationSpec
    meta: ResponseMeta


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
