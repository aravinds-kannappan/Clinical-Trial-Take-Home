"""API-level tests with the ClinicalTrials.gov client replaced by a recorded fixture."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.pipeline import Pipeline
from app.planner.base import PlanResult
from app.planner.llm import LLMPlanner
from app.schemas.common import AnalysisKind
from app.schemas.plan import Cohort, CohortFilters, QueryPlan
from tests.helpers import FakeCTGovClient


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.app.state.pipeline = Pipeline(FakeCTGovClient(total=2971), Settings(), None)
        c.app.state.llm_enabled = False
        yield c


def _post(client, **payload):
    r = client.post("/v1/visualize", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_time_series_contract(client):
    body = _post(client, query="How has the number of trials for this drug changed over time?", drug_name="Pembrolizumab", max_citations_per_datum=2)
    viz, meta = body["visualization"], body["meta"]
    assert viz["type"] == "time_series"
    assert viz["encoding"]["x"]["field"] == "period_start" and viz["encoding"]["y"]["field"] == "value"
    assert meta["planner_used"] == "rules" and meta["intent"] == "time_trend"
    assert meta["cohorts"][0]["filters_applied"]["intervention"] == "Pembrolizumab"
    assert meta["cohorts"][0]["truncated"] is True
    assert any("max_trials" in w for w in meta["warnings"])
    row = next(r for r in viz["data"] if r["value"] > 0)
    assert row["citations"][0]["nct_id"].startswith("NCT") and row["citations"][0]["evidence"][0]["excerpt"]


def test_bar_chart_contract(client):
    body = _post(client, query="How are these trials distributed across phases?", drug_name="Pembrolizumab")
    viz = body["visualization"]
    assert viz["type"] == "bar_chart"
    assert viz["encoding"]["x"]["field"] == "category"
    assert sum(r["value"] for r in viz["data"]) == 200


def test_grouped_bar_contract(client):
    body = _post(client, query="Compare phases for trials involving pembrolizumab vs nivolumab.")
    viz = body["visualization"]
    assert viz["type"] == "grouped_bar_chart"
    assert {r["series"] for r in viz["data"]} == {"pembrolizumab", "nivolumab"}
    assert len(body["meta"]["cohorts"]) == 2


def test_choropleth_contract(client):
    body = _post(client, query="Which countries have the most recruiting trials for melanoma?", top_n=5)
    viz = body["visualization"]
    assert viz["type"] == "choropleth_map" and len(viz["data"]) == 5
    assert viz["encoding"]["location"]["field"] == "iso3"
    assert viz["config"]["fallback"] == "bar_chart"


def test_network_contract(client):
    body = _post(client, query="Show a network of sponsors and drugs for melanoma trials")
    viz = body["visualization"]
    assert viz["type"] == "network_graph"
    assert viz["data"]["nodes"] and viz["data"]["edges"]
    assert viz["encoding"]["edges"]["source"] == "source"


def test_scatter_and_histogram_contract(client):
    s = _post(client, query="Enrollment vs start year for pembrolizumab trials")["visualization"]
    assert s["type"] == "scatter_plot" and s["data"][0]["nct_id"].startswith("NCT")
    h = _post(client, query="How large are pembrolizumab trials?")["visualization"]
    assert h["type"] == "histogram" and h["config"]["scale"] in ("linear", "log10")


def test_structured_fields_override_and_validate(client):
    body = _post(client, query="trials by phase", condition="melanoma", trial_phase="Phase 3", status="RECRUITING", start_year=2018, end_year=2024)
    f = body["meta"]["cohorts"][0]["filters_applied"]
    assert f["condition"] == "melanoma" and f["phases"] == ["Phase 3"] and f["statuses"] == ["RECRUITING"]
    assert f["start_year_from"] == 2018 and f["start_year_to"] == 2024
    r = client.post("/v1/visualize", json={"query": "trials by phase", "start_year": 2020, "end_year": 2010})
    assert r.status_code == 422
    r = client.post("/v1/visualize", json={"query": "x"})
    assert r.status_code == 422
    r = client.post("/v1/visualize", json={"query": "trials by phase", "bogus": 1})
    assert r.status_code == 422


def test_llm_required_but_missing(client):
    r = client.post("/v1/visualize", json={"query": "trials by phase", "planner": "llm"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "planner_unavailable"


def test_plan_endpoint(client):
    r = client.post("/v1/plan", json={"query": "How are lupus trials distributed across phases?"})
    assert r.status_code == 200 and r.json()["plan"]["analysis"] == "distribution"


def test_llm_plan_is_used_and_falls_back(client, monkeypatch):
    fake_plan = QueryPlan(analysis=AnalysisKind.DISTRIBUTION, cohorts=[Cohort(label="X", filters=CohortFilters(intervention="pembrolizumab"))], dimension="status", interpretation="mocked")

    async def good(self, query, today, context=None):
        return PlanResult(plan=fake_plan, planner_used="llm", llm_model="mock-model")

    async def bad(self, query, today, context=None):
        raise RuntimeError("boom")

    llm = LLMPlanner(api_key="x", model="mock-model")
    client.app.state.pipeline = Pipeline(FakeCTGovClient(), Settings(), llm)
    monkeypatch.setattr(LLMPlanner, "plan", good)
    body = _post(client, query="anything")
    assert body["meta"]["planner_used"] == "llm" and body["meta"]["llm_model"] == "mock-model"
    assert body["visualization"]["config"]["dimension"] == "status"

    monkeypatch.setattr(LLMPlanner, "plan", bad)
    body = _post(client, query="How are lupus trials distributed across phases?")
    assert body["meta"]["planner_used"] == "rules"
    assert any("LLM planner failed" in w for w in body["meta"]["warnings"])


def test_comparison_rows_share_category_order(client):
    body = _post(client, query="Compare sponsor categories for trials involving pembrolizumab vs nivolumab.")
    rows = body["visualization"]["data"]
    by_series = {}
    for r in rows:
        by_series.setdefault(r["series"], []).append(r["category"])
    orders = list(by_series.values())
    assert all(o == orders[0] for o in orders)


def test_llm_receives_structured_context(client, monkeypatch):
    seen = {}

    async def spy(self, query, today, context=None):
        seen["context"] = context
        raise RuntimeError("stop")

    client.app.state.pipeline = Pipeline(FakeCTGovClient(), Settings(), LLMPlanner(api_key="x", model="m"))
    monkeypatch.setattr(LLMPlanner, "plan", spy)
    _post(client, query="trials over time", drug_name="Pembrolizumab", start_year=2015)
    assert seen["context"] == {"drug_name": "Pembrolizumab", "start_year": 2015}
