"""FastAPI application: HTTP surface for the query-to-visualization pipeline."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.ctgov.client import CTGovClient, CTGovError
from app.pipeline import Pipeline
from app.planner.llm import LLMPlanner
from app.planner.router import PlannerUnavailable
from app.schemas.plan import PlanValidationError
from app.schemas.request import VisualizeRequest
from app.schemas.response import ErrorResponse, VisualizeResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    http = httpx.AsyncClient(timeout=settings.ctgov_timeout_seconds, headers={"User-Agent": "ct-viz-agent/0.1"})
    ctgov = CTGovClient(http, settings.ctgov_base_url, settings.ctgov_page_size, settings.ctgov_max_retries, settings.ctgov_cache_ttl_seconds)
    llm = LLMPlanner(settings.openai_api_key, settings.llm_model, settings.openai_base_url, settings.llm_timeout_seconds) if settings.openai_api_key else None
    if llm is None:
        logger.warning("No OPENAI_API_KEY set: using the rule-based planner only.")
    app.state.pipeline = Pipeline(ctgov, settings, llm)
    app.state.llm_enabled = llm is not None
    try:
        yield
    finally:
        await http.aclose()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Turns natural-language questions about clinical trials into cited visualization specifications backed by ClinicalTrials.gov.",
    lifespan=lifespan,
)


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message, "details": details})
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@app.exception_handler(PlanValidationError)
async def _plan_error(_: Request, exc: PlanValidationError):
    return _error(422, "plan_invalid", str(exc))


@app.exception_handler(PlannerUnavailable)
async def _planner_unavailable(_: Request, exc: PlannerUnavailable):
    return _error(400, "planner_unavailable", str(exc))


@app.exception_handler(CTGovError)
async def _ctgov_error(_: Request, exc: CTGovError):
    return _error(502, "upstream_error", str(exc), {"upstream_status": exc.status_code})


@app.get("/health", tags=["ops"])
async def health(request: Request):
    return {"status": "ok", "llm_planner": request.app.state.llm_enabled, "version": settings.app_version}


@app.post("/v1/visualize", response_model=VisualizeResponse, responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}}, tags=["agent"])
async def visualize(req: VisualizeRequest, request: Request) -> VisualizeResponse:
    """Answer a clinical-trial question with a cited visualization specification."""
    return await request.app.state.pipeline.run(req)


@app.post("/v1/plan", tags=["agent"])
async def plan_only(req: VisualizeRequest, request: Request):
    """Dry run: return the QueryPlan without fetching data (useful for debugging planners)."""
    result = await request.app.state.pipeline.plan(req)
    return {"plan": result.plan.model_dump(mode="json", exclude_none=True), "planner_used": result.planner_used, "llm_model": result.llm_model, "notes": result.notes, "warnings": result.warnings}


@app.get("/", include_in_schema=False)
async def demo_index():
    return FileResponse(DEMO_DIR / "index.html")
