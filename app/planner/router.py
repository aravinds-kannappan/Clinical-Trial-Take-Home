"""Pick a planner, run it, apply structured-field overrides, and validate the result."""

from __future__ import annotations

import logging
from datetime import date

from app.planner.base import PlanResult
from app.planner.llm import LLMPlanner
from app.planner.rules import RuleBasedPlanner
from app.schemas.common import PlannerChoice
from app.schemas.plan import PlanValidationError, QueryPlan, validate_plan
from app.schemas.request import VisualizeRequest

logger = logging.getLogger(__name__)


class PlannerUnavailable(Exception):
    """The caller required the LLM planner but none is configured."""


def apply_overrides(plan: QueryPlan, req: VisualizeRequest) -> list[str]:
    """Structured request fields win over anything inferred from the text. Returns notes."""
    notes: list[str] = []
    overrides = {
        "intervention": req.drug_name,
        "condition": req.condition,
        "sponsor": req.sponsor,
        "country": req.country,
    }
    for cohort in plan.cohorts:
        f = cohort.filters
        for key, value in overrides.items():
            if value is not None:
                if getattr(f, key) is None and f.free_text and f.free_text.lower() == value.lower():
                    f.free_text = None  # the planner guessed free text for the same entity; pin it instead
                setattr(f, key, value)
        if req.trial_phase is not None:
            f.phases = [req.trial_phase]
        if req.status is not None:
            f.statuses = [req.status]
        if req.start_year is not None:
            f.start_year_from = req.start_year
        if req.end_year is not None:
            f.start_year_to = req.end_year
    applied = [k for k, v in overrides.items() if v is not None]
    applied += [k for k, v in (("trial_phase", req.trial_phase), ("status", req.status), ("start_year", req.start_year), ("end_year", req.end_year)) if v is not None]
    if applied:
        notes.append("Structured request fields applied to every cohort: " + ", ".join(applied) + ".")
    if req.top_n is not None:
        plan.top_n = req.top_n
    if req.time_granularity is not None:
        plan.granularity = req.time_granularity
    if len(plan.cohorts) == 1 and plan.cohorts[0].filters.is_empty():
        notes.append("No entity filters were identified; results cover all ClinicalTrials.gov studies up to max_trials.")
    return notes


async def make_plan(req: VisualizeRequest, llm: LLMPlanner | None, today: date | None = None) -> PlanResult:
    today_str = (today or date.today()).isoformat()
    rules = RuleBasedPlanner()

    use_llm = req.planner == PlannerChoice.LLM or (req.planner == PlannerChoice.AUTO and llm is not None)
    if req.planner == PlannerChoice.LLM and llm is None:
        raise PlannerUnavailable("planner=llm was requested but no OPENAI_API_KEY is configured.")

    result: PlanResult
    if use_llm and llm is not None:
        try:
            context = req.model_dump(exclude_none=True, exclude={"query", "planner", "max_trials", "max_citations_per_datum"})
            result = await llm.plan(req.query, today_str, context or None)
        except Exception as exc:  # any LLM/validation failure -> deterministic fallback
            logger.warning("LLM planner failed (%s); falling back to rules", exc)
            result = await rules.plan(req.query, today_str)
            result.warnings.append(f"LLM planner failed ({type(exc).__name__}: {str(exc)[:160]}); used rule-based planner instead.")
    else:
        result = await rules.plan(req.query, today_str)

    result.notes.extend(apply_overrides(result.plan, req))
    try:
        result.plan = validate_plan(result.plan)
    except PlanValidationError as exc:
        raise PlanValidationError(f"Plan invalid after applying request fields: {exc}") from exc
    return result
