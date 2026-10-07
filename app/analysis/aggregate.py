"""Deterministic aggregations over TrialRecords, each producing cited rows.

No LLM is involved anywhere in this module: every number in a visualization is computed
here from API data, and every datum carries the trials (and field values) behind it.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.analysis.extract import dimension_values, numeric_value
from app.analysis.geo import country_codes
from app.ctgov.normalize import P_DESIGN, P_STATUS, TrialRecord, parse_date
from app.schemas.common import (
    PHASE_ORDER,
    Dimension,
    Measure,
    NumericField,
    SortOrder,
    TimeField,
    TimeGranularity,
)
from app.schemas.response import (
    CategoryRow,
    Citation,
    Evidence,
    GeoRow,
    HistogramBin,
    ScatterPoint,
    TimeRow,
)


@dataclass
class Bucket:
    key: str
    label: str
    records: list[TrialRecord] = field(default_factory=list)
    evidence: dict[str, list[Evidence]] = field(default_factory=lambda: defaultdict(list))

    def add(self, record: TrialRecord, ev: Evidence) -> None:
        if record.nct_id not in self.evidence:
            self.records.append(record)
        self.evidence[record.nct_id].append(ev)


@dataclass
class AggregationNotes:
    """Side information gathered while aggregating (surfaced in meta.warnings/notes)."""

    skipped_missing: int = 0
    skipped_reason: str = ""


# ----------------------------------------------------------------------------- citations
def make_citations(bucket: Bucket, cap: int, extra: dict[str, Evidence] | None = None) -> tuple[list[Citation], int, bool]:
    """Build capped citations for a bucket. Returns (citations, total, truncated)."""
    total = len(bucket.records)
    cites: list[Citation] = []
    for rec in bucket.records[:cap]:
        evidence = list(bucket.evidence[rec.nct_id])
        if extra and rec.nct_id in extra:
            evidence.append(extra[rec.nct_id])
        cites.append(Citation(nct_id=rec.nct_id, title=rec.title, url=rec.url, evidence=evidence))
    return cites, total, total > len(cites)


# ----------------------------------------------------------------------------- measures
def compute_measure(records: list[TrialRecord], measure: Measure) -> tuple[float, dict[str, Evidence]]:
    """Return the measure value plus, for enrollment measures, per-trial enrollment evidence."""
    if measure == Measure.TRIAL_COUNT:
        return float(len(records)), {}
    values: list[float] = []
    evidence: dict[str, Evidence] = {}
    for rec in records:
        v, ev = numeric_value(rec, NumericField.ENROLLMENT)
        if v is not None:
            values.append(v)
            evidence[rec.nct_id] = ev
    if not values:
        return 0.0, evidence
    if measure == Measure.ENROLLMENT_TOTAL:
        return float(sum(values)), evidence
    if measure == Measure.ENROLLMENT_MEAN:
        return round(statistics.fmean(values), 1), evidence
    if measure == Measure.ENROLLMENT_MEDIAN:
        return float(statistics.median(values)), evidence
    raise ValueError(measure)


# ----------------------------------------------------------------------------- group by
def _sort_buckets(buckets: list[Bucket], values: dict[str, float], dim: Dimension, sort: SortOrder) -> list[Bucket]:
    if sort == SortOrder.NATURAL:
        if dim == Dimension.PHASE:
            return sorted(buckets, key=lambda b: PHASE_ORDER.index(b.label) if b.label in PHASE_ORDER else 99)
        if dim == Dimension.START_YEAR:
            return sorted(buckets, key=lambda b: b.label)
        sort = SortOrder.DESC  # natural order for everything else is by value
    reverse = sort == SortOrder.DESC
    return sorted(buckets, key=lambda b: (values[b.key], b.label), reverse=reverse)


def group_by(
    records: list[TrialRecord],
    dim: Dimension,
    measure: Measure,
    *,
    top_n: int | None,
    sort: SortOrder,
    citation_cap: int,
    series: str | None = None,
) -> list[CategoryRow]:
    buckets: dict[str, Bucket] = {}
    for rec in records:
        for ex in dimension_values(rec, dim):
            buckets.setdefault(ex.key, Bucket(ex.key, ex.label)).add(rec, ex.evidence)

    values: dict[str, float] = {}
    extras: dict[str, dict[str, Evidence]] = {}
    for key, b in buckets.items():
        values[key], extras[key] = compute_measure(b.records, measure)

    ordered = _sort_buckets(list(buckets.values()), values, dim, sort)
    if top_n:
        ordered = ordered[:top_n]

    rows: list[CategoryRow] = []
    for b in ordered:
        cites, total, truncated = make_citations(b, citation_cap, extras[b.key])
        rows.append(
            CategoryRow(category=b.label, series=series, value=values[b.key], citations=cites, citation_count=total, citations_truncated=truncated)
        )
    return rows


# ----------------------------------------------------------------------------- time series
def _period_of(d: date, g: TimeGranularity) -> tuple[str, date]:
    if g == TimeGranularity.YEAR:
        return str(d.year), date(d.year, 1, 1)
    if g == TimeGranularity.QUARTER:
        q = (d.month - 1) // 3 + 1
        return f"{d.year}-Q{q}", date(d.year, 3 * (q - 1) + 1, 1)
    return f"{d.year}-{d.month:02d}", date(d.year, d.month, 1)


def _next_period(d: date, g: TimeGranularity) -> date:
    if g == TimeGranularity.YEAR:
        return date(d.year + 1, 1, 1)
    step = 3 if g == TimeGranularity.QUARTER else 1
    m = d.month + step
    return date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)


_TIME_PATHS = {
    TimeField.START_DATE: f"{P_STATUS}.startDateStruct.date",
    TimeField.PRIMARY_COMPLETION_DATE: f"{P_STATUS}.primaryCompletionDateStruct.date",
    TimeField.COMPLETION_DATE: f"{P_STATUS}.completionDateStruct.date",
    TimeField.FIRST_POSTED_DATE: f"{P_STATUS}.studyFirstPostDateStruct.date",
}


def time_series(
    records: list[TrialRecord],
    time_field: TimeField,
    granularity: TimeGranularity,
    measure: Measure,
    *,
    citation_cap: int,
    series: str | None = None,
    notes: AggregationNotes | None = None,
) -> list[TimeRow]:
    buckets: dict[date, Bucket] = {}
    skipped = 0
    for rec in records:
        raw = rec.date_for(time_field.value)
        d = parse_date(raw)
        if d is None:
            skipped += 1
            continue
        label, start = _period_of(d, granularity)
        buckets.setdefault(start, Bucket(label, label)).add(rec, Evidence(field=_TIME_PATHS[time_field], excerpt=raw))
    if notes is not None:
        notes.skipped_missing = skipped
        notes.skipped_reason = f"missing or unparseable {time_field.value}"
    if not buckets:
        return []

    rows: list[TimeRow] = []
    cursor, last = min(buckets), max(buckets)
    while cursor <= last:
        label, _ = _period_of(cursor, granularity)
        b = buckets.get(cursor)
        if b is None:
            rows.append(TimeRow(period=label, period_start=cursor.isoformat(), series=series, value=0.0, citation_count=0))
        else:
            value, extra = compute_measure(b.records, measure)
            cites, total, truncated = make_citations(b, citation_cap, extra)
            rows.append(TimeRow(period=label, period_start=cursor.isoformat(), series=series, value=value, citations=cites, citation_count=total, citations_truncated=truncated))
        cursor = _next_period(cursor, granularity)
    return rows


# ----------------------------------------------------------------------------- geographic
def by_country(records: list[TrialRecord], measure: Measure, *, top_n: int | None, citation_cap: int) -> list[GeoRow]:
    rows = group_by(records, Dimension.COUNTRY, measure, top_n=top_n, sort=SortOrder.DESC, citation_cap=citation_cap)
    out: list[GeoRow] = []
    for r in rows:
        alpha3, numeric = country_codes(r.category)
        out.append(GeoRow(country=r.category, iso3=alpha3, iso_numeric=numeric, value=r.value, citations=r.citations, citation_count=r.citation_count, citations_truncated=r.citations_truncated))
    return out


# ----------------------------------------------------------------------------- scatter
def scatter(
    records: list[TrialRecord],
    x: NumericField,
    y: NumericField,
    color: Dimension | None,
    *,
    citation_cap: int,
    notes: AggregationNotes | None = None,
) -> list[ScatterPoint]:
    points: list[ScatterPoint] = []
    skipped = 0
    for rec in records:
        xv, xev = numeric_value(rec, x)
        yv, yev = numeric_value(rec, y)
        if xv is None or yv is None:
            skipped += 1
            continue
        evidence = [xev, yev]
        color_label: str | None = None
        if color:
            vals = dimension_values(rec, color)
            if vals:
                color_label = vals[0].label
                evidence.append(vals[0].evidence)
        cites = [Citation(nct_id=rec.nct_id, title=rec.title, url=rec.url, evidence=evidence)] if citation_cap > 0 else []
        points.append(ScatterPoint(nct_id=rec.nct_id, label=rec.title, x=xv, y=yv, color=color_label, citations=cites, citation_count=1, citations_truncated=citation_cap == 0))
    if notes is not None:
        notes.skipped_missing = skipped
        notes.skipped_reason = f"missing {x.value} or {y.value}"
    return points


# ----------------------------------------------------------------------------- histogram
def _nice_linear_edges(lo: float, hi: float, bins: int) -> list[float]:
    span = max(hi - lo, 1e-9)
    raw = span / bins
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        width = m * mag
        if width >= raw:
            break
    start = math.floor(lo / width) * width
    edges = [start]
    while edges[-1] < hi:
        edges.append(round(edges[-1] + width, 10))
    return edges


def histogram(
    records: list[TrialRecord],
    field_name: NumericField,
    bins: int,
    *,
    citation_cap: int,
    notes: AggregationNotes | None = None,
) -> tuple[list[HistogramBin], str]:
    """Return (bins, scale). Uses log10 bins for heavy-tailed fields such as enrollment."""
    values: list[tuple[float, TrialRecord, Evidence]] = []
    skipped = 0
    for rec in records:
        v, ev = numeric_value(rec, field_name)
        if v is None:
            skipped += 1
        else:
            values.append((v, rec, ev))
    if notes is not None:
        notes.skipped_missing = skipped
        notes.skipped_reason = f"missing {field_name.value}"
    if not values:
        return [], "linear"

    lo, hi = min(v for v, _, _ in values), max(v for v, _, _ in values)
    positive = [v for v, _, _ in values if v > 0]
    use_log = field_name == NumericField.ENROLLMENT and positive and (max(positive) / max(min(positive), 1)) > 100
    if use_log:
        scale = "log10"
        max_exp = math.ceil(math.log10(max(positive)))
        edges = [0.0] + [float(10**e) for e in range(0, max_exp + 1)]
    else:
        scale = "linear"
        edges = _nice_linear_edges(lo, hi, bins)

    buckets = [Bucket(f"{edges[i]}", f"{_fmt(edges[i])}–{_fmt(edges[i + 1])}") for i in range(len(edges) - 1)]
    for v, rec, ev in values:
        idx = _bin_index(v, edges)
        buckets[idx].add(rec, ev)

    rows: list[HistogramBin] = []
    for i, b in enumerate(buckets):
        cites, total, truncated = make_citations(b, citation_cap)
        rows.append(HistogramBin(bin_start=edges[i], bin_end=edges[i + 1], bin_label=b.label, count=total, citations=cites, citation_count=total, citations_truncated=truncated))
    return rows, scale


def _bin_index(v: float, edges: list[float]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            return i
    return len(edges) - 2  # v == last edge goes in the last bin


def _fmt(v: float) -> str:
    if v >= 1000 and float(v).is_integer():
        return f"{int(v):,}"
    return str(int(v)) if float(v).is_integer() else f"{v:g}"
