"""Translate CohortFilters into ClinicalTrials.gov API v2 query parameters.

Reference: https://clinicaltrials.gov/data-api/api  (GET /api/v2/studies)
"""

from __future__ import annotations

import re

from app.schemas.common import PHASE_LABEL_TO_RAW
from app.schemas.plan import CohortFilters

# Projection of fields we need. Keeping it explicit makes responses small and parsing stable.
STUDY_FIELDS = [
    "NCTId",
    "BriefTitle",
    "OverallStatus",
    "StartDate",
    "PrimaryCompletionDate",
    "CompletionDate",
    "StudyFirstPostDate",
    "Phase",
    "StudyType",
    "DesignAllocation",
    "DesignPrimaryPurpose",
    "EnrollmentCount",
    "EnrollmentType",
    "LeadSponsorName",
    "LeadSponsorClass",
    "CollaboratorName",
    "CollaboratorClass",
    "Condition",
    "ConditionMeshTerm",
    "InterventionName",
    "InterventionType",
    "InterventionMeshTerm",
    "LocationCountry",
    "LocationFacility",
    "LocationCity",
    "OverallOfficialName",
    "OverallOfficialAffiliation",
    "Sex",
]

_UNSAFE = re.compile(r'[\[\]"\\]')


def _clean(text: str) -> str:
    """Strip characters that have meaning in the Essie query grammar."""
    return _UNSAFE.sub(" ", text).strip()


def build_advanced_filter(f: CohortFilters) -> str | None:
    """Compose the ``filter.advanced`` expression (Essie syntax) for structured constraints."""
    clauses: list[str] = []

    if f.start_year_from or f.start_year_to:
        lo = f"{f.start_year_from}-01-01" if f.start_year_from else "MIN"
        hi = f"{f.start_year_to}-12-31" if f.start_year_to else "MAX"
        clauses.append(f"AREA[StartDate]RANGE[{lo},{hi}]")

    if f.phases:
        raw_tokens: list[str] = []
        for phase in f.phases:
            raw_tokens.extend(PHASE_LABEL_TO_RAW.get(phase, []))
        raw_tokens = list(dict.fromkeys(raw_tokens))
        if raw_tokens:
            inner = " OR ".join(f"AREA[Phase]{t}" for t in raw_tokens)
            clauses.append(f"({inner})" if len(raw_tokens) > 1 else inner)

    if f.study_type:
        clauses.append(f"AREA[StudyType]{f.study_type.value}")

    if f.sponsor_class:
        clauses.append(f"AREA[LeadSponsorClass]{f.sponsor_class.value}")

    return " AND ".join(clauses) if clauses else None


def build_params(f: CohortFilters, page_size: int) -> dict[str, str]:
    params: dict[str, str] = {
        "format": "json",
        "pageSize": str(page_size),
        "countTotal": "true",
        "fields": ",".join(STUDY_FIELDS),
    }
    if f.intervention:
        params["query.intr"] = _clean(f.intervention)
    if f.condition:
        params["query.cond"] = _clean(f.condition)
    if f.sponsor:
        params["query.spons"] = _clean(f.sponsor)
    if f.country:
        params["query.locn"] = _clean(f.country)
    if f.free_text:
        params["query.term"] = _clean(f.free_text)
    if f.statuses:
        params["filter.overallStatus"] = ",".join(s.value for s in f.statuses)
    advanced = build_advanced_filter(f)
    if advanced:
        params["filter.advanced"] = advanced
    return params
