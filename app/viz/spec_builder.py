"""Map an executed QueryPlan plus aggregated rows to a VisualizationSpec.

The visualization type is a pure function of the analysis kind, and the ``encoding``
for each type is fixed and documented in the README, so a frontend can switch on
``type`` and read fields by name.
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import (
    ANALYSIS_TO_VIZ,
    DIMENSION_LABELS,
    ENTITY_LABELS,
    MEASURE_LABELS,
    MEASURE_UNITS,
    NUMERIC_FIELD_LABELS,
    TIME_FIELD_LABELS,
    AnalysisKind,
    Dimension,
    OverallStatus,
    SortOrder,
    VisualizationType,
)
from app.schemas.plan import CohortFilters, QueryPlan
from app.schemas.response import DataRows, VisualizationSpec


def _status_phrase(statuses: list[OverallStatus] | None) -> str:
    if not statuses:
        return ""
    return ", ".join(s.value.replace("_", " ").lower() for s in statuses) + " "


def _subject(filters: CohortFilters) -> str:
    desc = filters.describe()
    return f" for {desc}" if desc else ""


def _years(filters: CohortFilters) -> str:
    lo, hi = filters.start_year_from, filters.start_year_to
    if lo and hi:
        return f" ({lo}–{hi})"
    if lo:
        return f" (since {lo})"
    if hi:
        return f" (through {hi})"
    return ""


def build_title(plan: QueryPlan) -> tuple[str, str | None]:
    """Deterministic, template-based title and subtitle."""
    kind = plan.analysis
    f0 = plan.cohorts[0].filters
    measure = MEASURE_LABELS[plan.measure]
    status = _status_phrase(f0.statuses)

    if kind == AnalysisKind.TIME_TREND:
        title = f"{measure} by {TIME_FIELD_LABELS[plan.time_field].lower()} ({plan.granularity.value}){_subject(f0)}{_years(f0)}"
    elif kind == AnalysisKind.DISTRIBUTION:
        title = f"{status.capitalize()}{'trials' if plan.measure.value == 'trial_count' else measure} by {DIMENSION_LABELS[plan.dimension].lower()}{_subject(f0)}{_years(f0)}"
    elif kind == AnalysisKind.COMPARISON:
        labels = " vs ".join(c.label for c in plan.cohorts)
        title = f"{measure} by {DIMENSION_LABELS[plan.dimension].lower()}: {labels}"
    elif kind == AnalysisKind.GEOGRAPHIC:
        title = f"{status.capitalize()}{'trials' if plan.measure.value == 'trial_count' else measure} by country{_subject(f0)}{_years(f0)}"
    elif kind == AnalysisKind.RELATIONSHIP:
        types = plan.network.node_types
        rel = f"{ENTITY_LABELS[types[0]]} ↔ {ENTITY_LABELS[types[-1]]}" if len(types) == 2 else f"{ENTITY_LABELS[types[0]]} co-occurrence"
        title = f"{rel} network{_subject(f0)}{_years(f0)}"
    elif kind == AnalysisKind.SCATTER:
        title = f"{NUMERIC_FIELD_LABELS[plan.scatter.y]} vs {NUMERIC_FIELD_LABELS[plan.scatter.x].lower()}{_subject(f0)}{_years(f0)}"
    elif kind == AnalysisKind.HISTOGRAM:
        title = f"Distribution of {NUMERIC_FIELD_LABELS[plan.histogram.field].lower()}{_subject(f0)}{_years(f0)}"
    else:  # pragma: no cover
        title = "Clinical trials"

    title = title[0].upper() + title[1:]
    subtitle = plan.interpretation or None
    return title, subtitle


def _measure_channel(plan: QueryPlan, field_name: str = "value") -> dict[str, Any]:
    return {"field": field_name, "type": "quantitative", "title": MEASURE_LABELS[plan.measure], "unit": MEASURE_UNITS[plan.measure]}


def build_spec(plan: QueryPlan, data: DataRows, extra_config: dict[str, Any] | None = None) -> VisualizationSpec:
    viz_type: VisualizationType = ANALYSIS_TO_VIZ[plan.analysis]
    title, subtitle = build_title(plan)
    kind = plan.analysis
    config: dict[str, Any] = {"measure": plan.measure.value, "unit": MEASURE_UNITS[plan.measure]}
    encoding: dict[str, Any]

    if kind == AnalysisKind.DISTRIBUTION:
        encoding = {
            "x": {"field": "category", "type": "nominal", "title": DIMENSION_LABELS[plan.dimension]},
            "y": _measure_channel(plan),
            "tooltip": ["category", "value", "citation_count"],
        }
        config.update({"sort": plan.sort.value, "dimension": plan.dimension.value, "orientation": "vertical", "top_n": plan.top_n})

    elif kind == AnalysisKind.COMPARISON:
        encoding = {
            "x": {"field": "category", "type": "nominal", "title": DIMENSION_LABELS[plan.dimension]},
            "y": _measure_channel(plan),
            "series": {"field": "series", "type": "nominal", "title": "Cohort"},
            "tooltip": ["series", "category", "value", "citation_count"],
        }
        config.update({"sort": plan.sort.value, "dimension": plan.dimension.value, "grouping": "side_by_side", "series_order": [c.label for c in plan.cohorts], "top_n": plan.top_n})

    elif kind == AnalysisKind.TIME_TREND:
        encoding = {
            "x": {"field": "period_start", "type": "temporal", "title": TIME_FIELD_LABELS[plan.time_field], "label_field": "period"},
            "y": _measure_channel(plan),
            "tooltip": ["period", "value", "citation_count"],
        }
        config.update({"time_granularity": plan.granularity.value, "time_field": plan.time_field.value, "gaps_filled_with_zero": True, "mark": "line_with_points"})

    elif kind == AnalysisKind.GEOGRAPHIC:
        encoding = {
            "location": {"field": "iso3", "type": "nominal", "title": "Country", "lookup": "iso_3166_1_alpha3", "numeric_id_field": "iso_numeric"},
            "color": _measure_channel(plan),
            "label": {"field": "country", "type": "nominal", "title": "Country"},
            "tooltip": ["country", "value", "citation_count"],
        }
        config.update({"sort": "desc", "top_n": plan.top_n, "fallback": "bar_chart", "fallback_encoding": {"x": {"field": "country", "type": "nominal", "title": "Country"}, "y": _measure_channel(plan)}, "color_scale": "sequential"})

    elif kind == AnalysisKind.RELATIONSHIP:
        types = plan.network.node_types
        encoding = {
            "nodes": {"id": "id", "label": "label", "group": "type", "size": "weight", "size_title": "Trials mentioning entity"},
            "edges": {"source": "source", "target": "target", "weight": "weight", "weight_title": "Trials with both entities"},
            "tooltip": ["label", "type", "weight", "citation_count"],
        }
        config.update({"node_types": [t.value for t in types], "bipartite": len(types) == 2, "min_edge_weight": plan.network.min_edge_weight, "max_nodes": plan.network.max_nodes, "layout": "force_directed"})

    elif kind == AnalysisKind.SCATTER:
        encoding = {
            "x": {"field": "x", "type": "quantitative", "title": NUMERIC_FIELD_LABELS[plan.scatter.x]},
            "y": {"field": "y", "type": "quantitative", "title": NUMERIC_FIELD_LABELS[plan.scatter.y]},
            "label": {"field": "label", "type": "nominal", "title": "Trial"},
            "tooltip": ["nct_id", "label", "x", "y", "color"],
        }
        if plan.scatter.color:
            encoding["color"] = {"field": "color", "type": "nominal", "title": DIMENSION_LABELS[plan.scatter.color]}
        config.update({"x_field": plan.scatter.x.value, "y_field": plan.scatter.y.value, "y_scale": "log" if plan.scatter.y.value == "enrollment" else "linear", "point_is_trial": True})

    elif kind == AnalysisKind.HISTOGRAM:
        encoding = {
            "x": {"field": "bin_start", "type": "quantitative", "title": NUMERIC_FIELD_LABELS[plan.histogram.field], "bin_end_field": "bin_end", "label_field": "bin_label"},
            "y": {"field": "count", "type": "quantitative", "title": "Number of trials", "unit": "trials"},
            "tooltip": ["bin_label", "count"],
        }
        config.update({"field": plan.histogram.field.value, "requested_bins": plan.histogram.bins})
    else:  # pragma: no cover
        encoding = {}

    if extra_config:
        config.update(extra_config)
    return VisualizationSpec(type=viz_type, title=title, subtitle=subtitle, encoding=encoding, data=data, config=config)
