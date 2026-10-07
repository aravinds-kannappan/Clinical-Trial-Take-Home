"""The executor: run a QueryPlan end to end and assemble the response.

plan -> fetch cohort(s) -> normalize -> aggregate -> spec -> meta
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app.analysis import aggregate as agg
from app.analysis.network import build_network
from app.config import Settings
from app.ctgov.client import CTGovClient, FetchResult
from app.ctgov.normalize import TrialRecord, parse_study
from app.planner.base import PlanResult
from app.planner.llm import LLMPlanner
from app.planner.router import make_plan
from app.schemas.common import MULTI_VALUED_DIMENSIONS, PHASE_ORDER, AnalysisKind, Dimension
from app.schemas.plan import Cohort, QueryPlan
from app.schemas.request import VisualizeRequest
from app.schemas.response import CohortSummary, DataRows, ResponseMeta, VisualizeResponse
from app.viz.spec_builder import build_spec


class Pipeline:
    def __init__(self, ctgov: CTGovClient, settings: Settings, llm: LLMPlanner | None):
        self._ctgov = ctgov
        self._settings = settings
        self._llm = llm

    async def plan(self, req: VisualizeRequest) -> PlanResult:
        return await make_plan(req, self._llm)

    async def run(self, req: VisualizeRequest) -> VisualizeResponse:
        timings: dict[str, int] = {}
        t0 = time.perf_counter()
        planned = await self.plan(req)
        timings["planning"] = _ms(t0)

        plan = planned.plan
        max_trials = req.max_trials or self._settings.default_max_trials
        max_trials = min(max_trials, self._settings.hard_max_trials)
        cap = self._settings.default_citations_per_datum if req.max_citations_per_datum is None else req.max_citations_per_datum

        t1 = time.perf_counter()
        fetches = await asyncio.gather(*(self._ctgov.fetch_studies(c.filters, max_trials) for c in plan.cohorts))
        timings["fetch"] = _ms(t1)

        t2 = time.perf_counter()
        cohort_records = [[parse_study(s) for s in f.studies] for f in fetches]
        data, extra_config, agg_notes, agg_warnings = _aggregate(plan, cohort_records, cap)
        timings["aggregate"] = _ms(t2)

        spec = build_spec(plan, data, extra_config)
        summaries = [_summarize(c, f) for c, f in zip(plan.cohorts, fetches)]
        warnings = list(planned.warnings) + agg_warnings
        for s in summaries:
            if s.truncated:
                warnings.append(
                    f"Cohort '{s.label}': {s.total_matched} studies match but only the first {s.trials_analyzed} were analysed (max_trials). Raise max_trials for complete coverage."
                )
            if s.total_matched == 0:
                warnings.append(f"Cohort '{s.label}': no studies matched these filters.")

        meta = ResponseMeta(
            intent=plan.analysis,
            planner_used=planned.planner_used,  # type: ignore[arg-type]
            llm_model=planned.llm_model,
            plan=plan.model_dump(mode="json", exclude_none=True),
            cohorts=summaries,
            trials_analyzed=sum(len(r) for r in cohort_records),
            notes=planned.notes + agg_notes,
            warnings=warnings,
            generated_at=datetime.now(timezone.utc),
            timing_ms={**timings, "total": _ms(t0)},
        )
        return VisualizeResponse(visualization=spec, meta=meta)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _summarize(cohort: Cohort, fetch: FetchResult) -> CohortSummary:
    return CohortSummary(
        label=cohort.label,
        filters_applied=cohort.filters.model_dump(mode="json", exclude_none=True),
        total_matched=fetch.total_count,
        trials_analyzed=len(fetch.studies),
        truncated=fetch.truncated,
        api_requests=fetch.requests,
    )


def _aggregate(plan: QueryPlan, cohorts: list[list[TrialRecord]], cap: int) -> tuple[DataRows, dict, list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []
    extra: dict = {}
    kind = plan.analysis
    records = cohorts[0]

    if kind == AnalysisKind.DISTRIBUTION:
        rows = agg.group_by(records, plan.dimension, plan.measure, top_n=plan.top_n, sort=plan.sort, citation_cap=cap)
        if plan.dimension in MULTI_VALUED_DIMENSIONS:
            notes.append(f"A trial can have several {plan.dimension.value.replace('_', ' ')} values, so bar totals can exceed the number of trials.")
        if plan.dimension == Dimension.CONDITION:
            notes.append("Conditions are grouped by MeSH term when ClinicalTrials.gov provides one, otherwise by the free-text condition.")
        if plan.dimension == Dimension.INTERVENTION:
            notes.append("Placebo/sham/standard-of-care arms are excluded from intervention groupings.")
        return rows, extra, notes, warnings

    if kind == AnalysisKind.COMPARISON:
        rows = []
        for cohort, recs in zip(plan.cohorts, cohorts):
            rows.extend(agg.group_by(recs, plan.dimension, plan.measure, top_n=None, sort=plan.sort, citation_cap=cap, series=cohort.label))
        if plan.top_n:
            keep = _top_categories(rows, plan.top_n)
            rows = [r for r in rows if r.category in keep]
        rows = _order_comparison_rows(rows, plan)
        if plan.dimension in MULTI_VALUED_DIMENSIONS:
            notes.append("A trial can contribute to several categories for this dimension.")
        return rows, extra, notes, warnings

    if kind == AnalysisKind.TIME_TREND:
        an = agg.AggregationNotes()
        rows = agg.time_series(records, plan.time_field, plan.granularity, plan.measure, citation_cap=cap, notes=an)
        if an.skipped_missing:
            warnings.append(f"{an.skipped_missing} trials were skipped: {an.skipped_reason}.")
        notes.append("Periods with no trials are included with value 0 so the axis is continuous.")
        return rows, extra, notes, warnings

    if kind == AnalysisKind.GEOGRAPHIC:
        rows = agg.by_country(records, plan.measure, top_n=plan.top_n, citation_cap=cap)
        unresolved = [r.country for r in rows if r.iso3 is None]
        if unresolved:
            warnings.append("Could not map to ISO codes (render as bars): " + ", ".join(unresolved))
        notes.append("A trial is counted once for each country where it has at least one site.")
        return rows, extra, notes, warnings

    if kind == AnalysisKind.RELATIONSHIP:
        data = build_network(records, plan.network, citation_cap=cap)
        notes.append("Node weight = trials mentioning the entity; edge weight = trials in which both entities appear together. Placebo-like arms are excluded.")
        if not data.edges:
            warnings.append("No co-occurrences found above min_edge_weight; the graph has no edges.")
        return data, extra, notes, warnings

    if kind == AnalysisKind.SCATTER:
        an = agg.AggregationNotes()
        points = agg.scatter(records, plan.scatter.x, plan.scatter.y, plan.scatter.color, citation_cap=cap, notes=an)
        if an.skipped_missing:
            warnings.append(f"{an.skipped_missing} trials were skipped: {an.skipped_reason}.")
        return points, extra, notes, warnings

    if kind == AnalysisKind.HISTOGRAM:
        an = agg.AggregationNotes()
        bins, scale = agg.histogram(records, plan.histogram.field, plan.histogram.bins, citation_cap=cap, notes=an)
        extra["scale"] = scale
        if scale == "log10":
            notes.append("Enrollment is heavy-tailed, so bins are powers of ten.")
        if an.skipped_missing:
            warnings.append(f"{an.skipped_missing} trials were skipped: {an.skipped_reason}.")
        return bins, extra, notes, warnings

    raise ValueError(f"Unsupported analysis kind: {kind}")  # pragma: no cover


def _order_comparison_rows(rows, plan: QueryPlan):
    """Give every series the same category order: natural for ordered dimensions, else by total."""
    if plan.dimension in (Dimension.PHASE, Dimension.START_YEAR):
        order = {c: i for i, c in enumerate(PHASE_ORDER)} if plan.dimension == Dimension.PHASE else {}
        key = (lambda c: order.get(c, 99)) if order else (lambda c: c)
    else:
        totals: dict[str, float] = {}
        for r in rows:
            totals[r.category] = totals.get(r.category, 0.0) + r.value
        key = lambda c: -totals[c]
    series_rank = {c.label: i for i, c in enumerate(plan.cohorts)}
    return sorted(rows, key=lambda r: (series_rank.get(r.series, 99), key(r.category), r.category))


def _top_categories(rows, n: int) -> set[str]:
    totals: dict[str, float] = {}
    for r in rows:
        totals[r.category] = totals.get(r.category, 0.0) + r.value
    return {c for c, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:n]}
