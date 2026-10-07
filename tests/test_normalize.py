from datetime import date

from app.ctgov.normalize import parse_date, parse_study, parse_year, phase_label
from app.schemas.common import Phase
from tests.helpers import fixture_records, load_fixture


def test_phase_label_combinations():
    assert phase_label([]) == Phase.NOT_REPORTED
    assert phase_label(["NA"]) == Phase.NOT_APPLICABLE
    assert phase_label(["PHASE1", "PHASE2"]) == Phase.PHASE1_PHASE2
    assert phase_label(["PHASE2", "PHASE3"]) == Phase.PHASE2_PHASE3
    assert phase_label(["PHASE3"]) == Phase.PHASE3
    assert phase_label(["EARLY_PHASE1"]) == Phase.EARLY_PHASE1


def test_partial_dates():
    assert parse_year("2027-12") == 2027
    assert parse_year("2024-07-03") == 2024
    assert parse_year(None) is None
    assert parse_date("2027-12") == date(2027, 12, 1)
    assert parse_date("garbage") is None


def test_parse_study_dedupes_countries_and_keeps_paths():
    raw = load_fixture()["studies"][0]
    rec = parse_study(raw)
    assert rec.nct_id.startswith("NCT")
    assert len(rec.countries) == len(set(rec.countries))
    assert rec.site_count >= len(rec.countries)
    if rec.locations_country:
        assert rec.locations_country[0].path.endswith("locations[0].country")
    if rec.interventions:
        assert rec.interventions[0].name_path.endswith("interventions[0].name")


def test_fixture_parses_without_errors():
    records = fixture_records()
    assert len(records) == 200
    assert all(r.nct_id for r in records)
    assert any(r.enrollment is not None for r in records)
