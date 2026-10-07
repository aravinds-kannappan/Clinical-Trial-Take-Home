import pytest

from app.schemas.common import AnalysisKind, Dimension, EntityType, NumericField
from app.schemas.plan import Cohort, CohortFilters, NetworkSpec, PlanValidationError, QueryPlan, ScatterSpec, validate_plan


def _cohort(label="a", **kw):
    return Cohort(label=label, filters=CohortFilters(**kw))


def test_comparison_requires_two_cohorts():
    with pytest.raises(PlanValidationError):
        validate_plan(QueryPlan(analysis=AnalysisKind.COMPARISON, cohorts=[_cohort()]))


def test_single_cohort_kinds_reject_multiple():
    with pytest.raises(PlanValidationError):
        validate_plan(QueryPlan(analysis=AnalysisKind.TIME_TREND, cohorts=[_cohort("a"), _cohort("b")]))


def test_year_order():
    with pytest.raises(PlanValidationError):
        validate_plan(QueryPlan(analysis=AnalysisKind.TIME_TREND, cohorts=[_cohort(start_year_from=2020, start_year_to=2015)]))


def test_defaults_are_filled():
    p = validate_plan(QueryPlan(analysis=AnalysisKind.RELATIONSHIP, cohorts=[_cohort()]))
    assert p.network.node_types == [EntityType.SPONSOR, EntityType.INTERVENTION]
    p = validate_plan(QueryPlan(analysis=AnalysisKind.GEOGRAPHIC, cohorts=[_cohort()]))
    assert p.dimension == Dimension.COUNTRY
    p = validate_plan(QueryPlan(analysis=AnalysisKind.HISTOGRAM, cohorts=[_cohort()]))
    assert p.histogram.field == NumericField.ENROLLMENT
    p = validate_plan(QueryPlan(analysis=AnalysisKind.COMPARISON, cohorts=[_cohort("a"), _cohort("b")]))
    assert p.dimension == Dimension.PHASE


def test_duplicate_network_types_collapse():
    p = validate_plan(QueryPlan(analysis=AnalysisKind.RELATIONSHIP, cohorts=[_cohort()], network=NetworkSpec(node_types=[EntityType.INTERVENTION, EntityType.INTERVENTION])))
    assert p.network.node_types == [EntityType.INTERVENTION]


def test_scatter_axes_must_differ():
    with pytest.raises(PlanValidationError):
        validate_plan(QueryPlan(analysis=AnalysisKind.SCATTER, cohorts=[_cohort()], scatter=ScatterSpec(x=NumericField.ENROLLMENT, y=NumericField.ENROLLMENT)))


def test_distribution_needs_dimension():
    with pytest.raises(PlanValidationError):
        validate_plan(QueryPlan(analysis=AnalysisKind.DISTRIBUTION, cohorts=[_cohort()]))
