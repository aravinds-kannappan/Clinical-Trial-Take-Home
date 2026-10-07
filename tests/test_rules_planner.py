from datetime import date

import pytest

from app.planner.rules import plan_with_rules
from app.schemas.common import AnalysisKind, Dimension, EntityType, OverallStatus, Phase

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize(
    "query,kind,dimension",
    [
        ("How has the number of trials for pembrolizumab changed per year since 2015?", AnalysisKind.TIME_TREND, None),
        ("How many trials started each year for breast cancer?", AnalysisKind.TIME_TREND, None),
        ("How are lupus trials distributed across phases?", AnalysisKind.DISTRIBUTION, Dimension.PHASE),
        ("What are the most common intervention types for melanoma trials?", AnalysisKind.DISTRIBUTION, Dimension.INTERVENTION_TYPE),
        ("Compare phases for trials involving pembrolizumab vs nivolumab.", AnalysisKind.COMPARISON, Dimension.PHASE),
        ("Compare sponsor categories across two conditions: psoriasis and asthma.", AnalysisKind.COMPARISON, Dimension.SPONSOR_CLASS),
        ("Which countries have the most recruiting trials for lupus?", AnalysisKind.GEOGRAPHIC, Dimension.COUNTRY),
        ("Show a network of sponsors ↔ drugs for breast cancer trials.", AnalysisKind.RELATIONSHIP, None),
        ("Enrollment vs start year for phase 3 pembrolizumab trials", AnalysisKind.SCATTER, None),
        ("How large are trials for Alzheimer's disease?", AnalysisKind.HISTOGRAM, None),
    ],
)
def test_kind_and_dimension(query, kind, dimension):
    plan = plan_with_rules(query, TODAY).plan
    assert plan.analysis == kind
    assert plan.dimension == dimension


def test_entities_and_years():
    plan = plan_with_rules("How has the number of trials for pembrolizumab changed per year since 2015?", TODAY).plan
    f = plan.cohorts[0].filters
    assert f.intervention == "pembrolizumab"
    assert f.start_year_from == 2015


def test_comparison_cohorts():
    plan = plan_with_rules("Compare phases for trials involving pembrolizumab vs nivolumab.", TODAY).plan
    assert [c.filters.intervention for c in plan.cohorts] == ["pembrolizumab", "nivolumab"]


def test_status_country_sponsor_filters():
    plan = plan_with_rules("Top 10 sponsors of covid-19 trials sponsored by Pfizer in Germany since 2020", TODAY).plan
    f = plan.cohorts[0].filters
    assert plan.dimension == Dimension.SPONSOR and plan.top_n == 10
    assert f.condition == "covid-19" and f.sponsor == "Pfizer" and f.country == "Germany" and f.start_year_from == 2020
    plan = plan_with_rules("Which countries have the most recruiting trials for lupus?", TODAY).plan
    assert plan.cohorts[0].filters.statuses == [OverallStatus.RECRUITING]


def test_phase_filter_and_scatter_axes():
    plan = plan_with_rules("Enrollment vs start year for phase 3 pembrolizumab trials", TODAY).plan
    assert plan.cohorts[0].filters.phases == [Phase.PHASE3]
    assert plan.scatter.x.value == "start_year" and plan.scatter.y.value == "enrollment"


def test_network_types():
    plan = plan_with_rules("Which drugs frequently co-occur in combination studies for melanoma (drug ↔ drug network)?", TODAY).plan
    assert plan.network.node_types == [EntityType.INTERVENTION]
    assert plan.cohorts[0].filters.condition == "melanoma"


def test_unknown_entity_falls_back_to_free_text_with_note():
    result = plan_with_rules("How are trials for Zyxovantrex distributed across phases?", TODAY)
    assert result.plan.cohorts[0].filters.free_text == "Zyxovantrex"
    assert any("free text" in n for n in result.notes)


def test_placeholder_entity_is_ignored():
    plan = plan_with_rules("How has the number of trials for this drug changed over time?", TODAY).plan
    assert plan.cohorts[0].filters.is_empty()
