from app.analysis import aggregate as agg
from app.analysis.network import build_network
from app.schemas.common import Dimension, EntityType, Measure, NumericField, SortOrder, TimeField, TimeGranularity
from app.schemas.plan import NetworkSpec
from tests.helpers import fixture_records

RECORDS = fixture_records()


def test_group_by_single_valued_dimension_partitions_records():
    rows = agg.group_by(RECORDS, Dimension.PHASE, Measure.TRIAL_COUNT, top_n=None, sort=SortOrder.NATURAL, citation_cap=3)
    assert sum(r.value for r in rows) == len(RECORDS)
    assert all(len(r.citations) <= 3 for r in rows)
    assert all(r.citation_count == r.value for r in rows)
    assert all(c.evidence[0].field == "protocolSection.designModule.phases" for r in rows for c in r.citations)
    labels = [r.category for r in rows]
    assert labels == sorted(labels, key=lambda l: ["Early Phase 1", "Phase 1", "Phase 1/Phase 2", "Phase 2", "Phase 2/Phase 3", "Phase 3", "Phase 4", "Not Applicable", "Not Reported"].index(l))


def test_group_by_multi_valued_dimension_and_top_n():
    rows = agg.group_by(RECORDS, Dimension.COUNTRY, Measure.TRIAL_COUNT, top_n=5, sort=SortOrder.DESC, citation_cap=1)
    assert len(rows) == 5
    assert rows[0].value >= rows[-1].value
    assert rows[0].citations[0].evidence[0].field.endswith(".country")


def test_enrollment_measure_adds_enrollment_evidence():
    rows = agg.group_by(RECORDS, Dimension.SPONSOR_CLASS, Measure.ENROLLMENT_TOTAL, top_n=None, sort=SortOrder.DESC, citation_cap=2)
    cite = rows[0].citations[0]
    assert any(e.field.endswith("enrollmentInfo.count") for e in cite.evidence)


def test_time_series_fills_gaps_and_is_chronological():
    rows = agg.time_series(RECORDS, TimeField.START_DATE, TimeGranularity.YEAR, Measure.TRIAL_COUNT, citation_cap=1)
    years = [int(r.period) for r in rows]
    assert years == list(range(years[0], years[-1] + 1))
    assert sum(r.value for r in rows) <= len(RECORDS)
    q = agg.time_series(RECORDS[:50], TimeField.START_DATE, TimeGranularity.QUARTER, Measure.TRIAL_COUNT, citation_cap=1)
    assert all("-Q" in r.period for r in q)


def test_histogram_log_bins_cover_all_values():
    bins, scale = agg.histogram(RECORDS, NumericField.ENROLLMENT, 10, citation_cap=1)
    assert scale == "log10"
    assert sum(b.count for b in bins) == sum(1 for r in RECORDS if r.enrollment is not None)
    assert bins[0].bin_start == 0.0


def test_scatter_points_are_trials():
    pts = agg.scatter(RECORDS, NumericField.START_YEAR, NumericField.ENROLLMENT, Dimension.PHASE, citation_cap=1)
    assert pts and all(p.citation_count == 1 for p in pts)
    assert all(p.color for p in pts)


def test_by_country_resolves_iso_codes():
    rows = agg.by_country(RECORDS, Measure.TRIAL_COUNT, top_n=10, citation_cap=1)
    us = next(r for r in rows if r.country == "United States")
    assert us.iso3 == "USA" and us.iso_numeric == "840"


def test_bipartite_network_edges_connect_different_types():
    data = build_network(RECORDS, NetworkSpec(node_types=[EntityType.SPONSOR, EntityType.INTERVENTION], max_nodes=30), citation_cap=2)
    ids = {n.id for n in data.nodes}
    assert data.edges
    for e in data.edges:
        assert e.source in ids and e.target in ids
        assert e.source.startswith("sponsor:") and e.target.startswith("intervention:")
        assert e.citation_count == e.weight
        assert len(e.citations[0].evidence) == 2


def test_cooccurrence_network_single_type():
    data = build_network(RECORDS, NetworkSpec(node_types=[EntityType.INTERVENTION], max_nodes=20, min_edge_weight=2), citation_cap=1)
    assert all(n.type == "intervention" for n in data.nodes)
    assert all(e.weight >= 2 for e in data.edges)
    assert not any("placebo" in n.label.lower() for n in data.nodes)
