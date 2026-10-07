from app.ctgov.query_builder import build_advanced_filter, build_params
from app.schemas.common import OverallStatus, Phase, SponsorClass, StudyType
from app.schemas.plan import CohortFilters


def test_basic_params():
    p = build_params(CohortFilters(intervention="pembrolizumab", condition="melanoma"), page_size=100)
    assert p["query.intr"] == "pembrolizumab"
    assert p["query.cond"] == "melanoma"
    assert p["pageSize"] == "100"
    assert p["countTotal"] == "true"
    assert "NCTId" in p["fields"]


def test_advanced_filter_composition():
    f = CohortFilters(start_year_from=2015, phases=[Phase.PHASE1_PHASE2], study_type=StudyType.INTERVENTIONAL, sponsor_class=SponsorClass.INDUSTRY)
    adv = build_advanced_filter(f)
    assert "AREA[StartDate]RANGE[2015-01-01,MAX]" in adv
    assert "(AREA[Phase]PHASE1 OR AREA[Phase]PHASE2)" in adv
    assert "AREA[StudyType]INTERVENTIONAL" in adv
    assert "AREA[LeadSponsorClass]INDUSTRY" in adv
    assert adv.count(" AND ") == 3


def test_status_and_unsafe_chars():
    p = build_params(CohortFilters(free_text='lupus [nephritis] "x"', statuses=[OverallStatus.RECRUITING, OverallStatus.COMPLETED]), 10)
    assert p["filter.overallStatus"] == "RECRUITING,COMPLETED"
    assert "[" not in p["query.term"] and '"' not in p["query.term"]
    assert "filter.advanced" not in p
