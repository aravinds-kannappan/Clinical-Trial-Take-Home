"""LLM planner: turns a question into a QueryPlan using OpenAI structured outputs.

Design choices that keep this step hallucination-resistant:
- The model can only emit a ``QueryPlan`` (closed schema, enums everywhere). It never sees
  trial data and never produces numbers that end up in a chart.
- Its output is re-validated by ``validate_plan``; failure falls back to the rule planner.
- Entity strings it extracts are used only as *search terms* against the real API; the
  response always reports the actual filters applied and the actual match counts.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.planner.base import PlanResult
from app.schemas.plan import QueryPlan, validate_plan

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert questions about clinical trials into a QueryPlan that a deterministic
backend executes against the ClinicalTrials.gov API. You do not answer the question; you only
describe what to fetch and how to aggregate it.

Choose `analysis` by question type:
- time_trend: counts over time ("per year", "since 2015", "over time", "trend").
- distribution: breakdown of one set of trials by one categorical field ("by phase", "most common intervention types", "how are ... distributed").
- comparison: the same breakdown for two or more named cohorts ("A vs B", "compare ... across two conditions"). One cohort per compared entity, labelled with the entity name.
- geographic: counts by country ("which countries", "where").
- relationship: a network between entities ("network of sponsors and drugs", "which drugs co-occur"). node_types: two types for a bipartite graph, one type (e.g. [intervention]) for a co-occurrence graph.
- scatter: one point per trial, two numeric fields ("enrollment vs start year").
- histogram: distribution of one numeric field ("how large are trials", "distribution of enrollment").

Filters: put drug/intervention names in `intervention`, diseases in `condition`, organisations in
`sponsor`, countries in `country`. Use `free_text` only when you genuinely cannot tell what kind of
entity a phrase is. Only set `statuses`, `phases`, `study_type`, `sponsor_class` or year bounds when
the question asks for them. Resolve relative periods ("last 5 years") using today's date.
"Recruiting" means statuses=[RECRUITING]; "ongoing/active" means RECRUITING, ACTIVE_NOT_RECRUITING,
ENROLLING_BY_INVITATION and NOT_YET_RECRUITING.

Set `top_n` (10-25) when a dimension has many values (sponsor, intervention, condition, country,
collaborator) or when the question says "top N" or "most common". Leave `dimension` null unless the
analysis is distribution or comparison. Keep `sort` = natural unless the user asks for an order.
Write one plain sentence in `interpretation` describing how you read the question, including any
assumption you made."""


class LLMPlanner:
    def __init__(self, api_key: str, model: str, base_url: str | None = None, timeout: float = 40.0):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        self.model = model

    async def plan(self, query: str, today: str, context: dict | None = None) -> PlanResult:
        """``context`` carries structured request fields so labels and interpretation match them."""
        kwargs: dict = {}
        if self.model.startswith("gpt-5"):
            kwargs["reasoning"] = {"effort": "low"}
        response = await self._client.responses.parse(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": _user_message(query, today, context)}],
            text_format=QueryPlan,
            **kwargs,
        )
        parsed: QueryPlan | None = response.output_parsed
        if parsed is None:
            raise ValueError("LLM returned no parsable plan (possibly a refusal)")
        plan = validate_plan(parsed)
        return PlanResult(plan=plan, planner_used="llm", llm_model=self.model)


def _user_message(query: str, today: str, context: dict | None) -> str:
    lines = [f"Today's date: {today}"]
    if context:
        fields = ", ".join(f"{k}={v}" for k, v in context.items() if v is not None)
        if fields:
            lines.append(f"Structured fields supplied by the caller (these are authoritative and will be applied as filters): {fields}")
    lines.append(f"Question: {query}")
    return "\n\n".join(lines)
