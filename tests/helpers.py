"""Shared test helpers: fixture loading and a fake ClinicalTrials.gov client."""

from __future__ import annotations

import json
from pathlib import Path

from app.ctgov.client import FetchResult
from app.ctgov.normalize import TrialRecord, parse_study
from app.schemas.plan import CohortFilters

FIXTURE = Path(__file__).parent / "fixtures" / "pembrolizumab_200.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def fixture_records(n: int | None = None) -> list[TrialRecord]:
    studies = load_fixture()["studies"]
    return [parse_study(s) for s in (studies[:n] if n else studies)]


class FakeCTGovClient:
    """Serves the recorded fixture for any filters; records the calls it received."""

    def __init__(self, studies: list[dict] | None = None, total: int | None = None):
        self.studies = studies if studies is not None else load_fixture()["studies"]
        self.total = total if total is not None else len(self.studies)
        self.calls: list[tuple[CohortFilters, int]] = []

    async def fetch_studies(self, filters: CohortFilters, max_trials: int) -> FetchResult:
        self.calls.append((filters, max_trials))
        page = self.studies[:max_trials]
        return FetchResult(studies=page, total_count=self.total, truncated=len(page) < self.total, requests=[f"fake://studies?{filters.model_dump_json(exclude_none=True)}"])
