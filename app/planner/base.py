"""Planner interface and the result type shared by all planners."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.plan import QueryPlan


@dataclass
class PlanResult:
    plan: QueryPlan
    planner_used: str  # "llm" | "rules"
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    llm_model: str | None = None


class Planner(Protocol):
    async def plan(self, query: str, today: str, context: dict | None = None) -> PlanResult: ...
