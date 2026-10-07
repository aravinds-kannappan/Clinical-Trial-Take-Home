"""Deterministic, dependency-free planner.

It is the fallback when no LLM is configured (or when the LLM plan fails validation) and
it doubles as a regression oracle in tests. It is intentionally conservative: when it
cannot tell whether a phrase is a drug or a disease it uses a free-text search instead of
guessing, and it says so in ``notes``.
"""

from __future__ import annotations

import re
from datetime import date

from app.analysis.geo import country_codes
from app.planner.base import PlanResult
from app.schemas.common import (
    AnalysisKind,
    Dimension,
    EntityType,
    Measure,
    NumericField,
    OverallStatus,
    Phase,
    SortOrder,
    TimeGranularity,
)
from app.schemas.plan import (
    Cohort,
    CohortFilters,
    HistogramSpec,
    NetworkSpec,
    QueryPlan,
    ScatterSpec,
    validate_plan,
)

# ----------------------------------------------------------------------------- vocab
_KIND_PATTERNS: list[tuple[AnalysisKind, re.Pattern]] = [
    (AnalysisKind.RELATIONSHIP, re.compile(r"\bnetwork\b|co-?occur|\bgraph\b|↔|<->|relationships?\s+between|combination (?:studies|trials)|combined with|pairs? of", re.I)),
    (AnalysisKind.SCATTER, re.compile(r"\bscatter\b|correlat|\bagainst\b|(?:enrollment|duration|sites?)\s+(?:vs\.?|versus|by)\s+(?:start\s+)?(?:year|enrollment|duration)", re.I)),
    (AnalysisKind.HISTOGRAM, re.compile(r"\bhistogram\b|distribution of (?:the )?(?:enrollment|sample size|trial size|duration|number of (?:sites|participants))|how (?:large|big|long)\b|enrollment sizes?|sample sizes?", re.I)),
    (AnalysisKind.COMPARISON, re.compile(r"\bcompare\b|\bcomparison\b|\bvs\.?\b|\bversus\b|compared (?:to|with)|difference between", re.I)),
    (AnalysisKind.TIME_TREND, re.compile(r"over time|per (?:year|month|quarter)|each (?:year|month|quarter)|every year|by year|yearly|annual|monthly|quarterly|\btrend|changed over|growth|timeline|how many .* (?:started|began) (?:each|per)", re.I)),
    (AnalysisKind.GEOGRAPHIC, re.compile(r"\bcountr(?:y|ies)\b|\bwhere\b|geograph|\bmap\b|\bregions?\b|worldwide|globally|locations?", re.I)),
]

_DIMENSION_PATTERNS: list[tuple[Dimension, re.Pattern]] = [
    (Dimension.SPONSOR_CLASS, re.compile(r"sponsor (?:class|type|categor)|funder|funding (?:source|type)|industry[- ]sponsored|industry (?:vs|versus|or|and) (?:academic|non)", re.I)),
    (Dimension.COLLABORATOR, re.compile(r"collaborator", re.I)),
    (Dimension.SPONSOR, re.compile(r"\bsponsors?\b", re.I)),
    (Dimension.INTERVENTION_TYPE, re.compile(r"intervention types?|types? of interventions?", re.I)),
    (Dimension.PHASE, re.compile(r"\bphases?\b", re.I)),
    (Dimension.STATUS, re.compile(r"\bstatus(?:es)?\b", re.I)),
    (Dimension.COUNTRY, re.compile(r"\bcountr(?:y|ies)\b", re.I)),
    (Dimension.STUDY_TYPE, re.compile(r"study types?|observational (?:vs|versus|or) interventional", re.I)),
    (Dimension.PRIMARY_PURPOSE, re.compile(r"\bpurposes?\b", re.I)),
    (Dimension.ALLOCATION, re.compile(r"\ballocation\b|randomi[sz]ed (?:vs|versus|or)", re.I)),
    (Dimension.SEX, re.compile(r"\bsex\b|\bgender\b", re.I)),
    (Dimension.CONDITION, re.compile(r"\bconditions?\b|\bdiseases?\b|\bindications?\b", re.I)),
    (Dimension.INTERVENTION, re.compile(r"\bdrugs?\b|\binterventions?\b|\btreatments?\b", re.I)),
    (Dimension.START_YEAR, re.compile(r"\bstart year\b|\byear\b", re.I)),
]

_ENTITY_WORDS: list[tuple[EntityType, re.Pattern]] = [
    (EntityType.SPONSOR, re.compile(r"\bsponsors?\b", re.I)),
    (EntityType.COLLABORATOR, re.compile(r"\bcollaborators?\b", re.I)),
    (EntityType.INTERVENTION, re.compile(r"\bdrugs?\b|\binterventions?\b|\btreatments?\b|\bcompounds?\b", re.I)),
    (EntityType.CONDITION, re.compile(r"\bconditions?\b|\bdiseases?\b|\bindications?\b", re.I)),
    (EntityType.COUNTRY, re.compile(r"\bcountr(?:y|ies)\b|\bsites?\b", re.I)),
    (EntityType.INVESTIGATOR, re.compile(r"\binvestigators?\b|\bprincipal investigators?\b|\bPIs?\b")),
]

_STATUS_PATTERNS: list[tuple[re.Pattern, list[OverallStatus]]] = [
    (re.compile(r"not yet recruiting", re.I), [OverallStatus.NOT_YET_RECRUITING]),
    (re.compile(r"\brecruiting\b", re.I), [OverallStatus.RECRUITING]),
    (re.compile(r"\bcompleted\b", re.I), [OverallStatus.COMPLETED]),
    (re.compile(r"\bterminated\b", re.I), [OverallStatus.TERMINATED]),
    (re.compile(r"\bwithdrawn\b", re.I), [OverallStatus.WITHDRAWN]),
    (re.compile(r"\bsuspended\b", re.I), [OverallStatus.SUSPENDED]),
    (re.compile(r"\b(?:ongoing|active|open)\b", re.I), [OverallStatus.RECRUITING, OverallStatus.ACTIVE_NOT_RECRUITING, OverallStatus.ENROLLING_BY_INVITATION, OverallStatus.NOT_YET_RECRUITING]),
]

_PHASE_WORDS = {"1": Phase.PHASE1, "i": Phase.PHASE1, "2": Phase.PHASE2, "ii": Phase.PHASE2, "3": Phase.PHASE3, "iii": Phase.PHASE3, "4": Phase.PHASE4, "iv": Phase.PHASE4}

_CONDITION_HINT = re.compile(
    r"cancer|carcinoma|tumou?r|leuk|lymphoma|melanoma|myeloma|sarcoma|glioma|disease|syndrome|disorder|diabetes|lupus|asthma|covid|sars|hiv|infection|depression|anxiety|schizophrenia|obesity|hypertension|stroke|failure|injury|pain|arthritis|sclerosis|psoriasis|dementia|alzheimer|parkinson|epilepsy|migraine|fibrosis|hepatitis|malaria|tuberculosis|sepsis|anemia|itis\b|osis\b|emia\b|pathy\b|oma\b",
    re.I,
)
_DRUG_HINT = re.compile(
    r"(?:mab|nib|ciclib|parib|lisib|rafenib|tinib|vir|statin|pril|sartan|olol|azole|cillin|mycin|cycline|prazole|tide|gliptin|gliflozin|lutamide|platin|taxel|rubicin|semide|zepam|dipine|done|lone|sone|zumab|ximab|umab|vastatin|setron|triptan|afil|oxetine|ipramine|azepam|metformin|insulin|aspirin|ibuprofen|vaccine|cabi|cel)\b",
    re.I,
)

_ENTITY_STOP = {
    "this drug", "the drug", "that drug", "this condition", "the condition", "this disease", "each", "the", "this",
    "trials", "studies", "study", "trial", "clinical trials", "clinical trial", "a drug", "a condition", "the number",
    "number", "drug", "condition", "disease", "sponsors", "drugs", "phase", "phases", "conditions", "diseases",
    "two conditions", "two drugs", "these drugs", "these conditions", "all", "time", "year", "years",
}
_TERMINATORS = r"(?= trials?\b| studies\b| study\b| since\b| per\b| by\b| over\b| across\b| between\b| from\b| started\b| that\b| which\b| sponsored\b| recruiting\b| changed\b| distributed\b| compared\b| vs\.?\b| versus\b| and\b| in \d| after\b| before\b| during\b| within\b| with\b| each\b| as\b| at\b| in\b| for\b|[,.?;:()]|$)"
_ENTITY_RE = re.compile(r"\b(?:for|involving|of|on|about|using|with|investigating|testing|evaluating|in)\s+(?:the\s+|this\s+|these\s+)?(?:drug\s+|condition\s+|disease\s+|patients with\s+)?([A-Za-z][\w\-'/ ]*?)" + _TERMINATORS, re.I)
_PREFIX_ENTITY_RE = re.compile(r"\b((?:[A-Za-z][\w\-'/]+ ){0,3}[A-Za-z][\w\-'/]+)\s+(?:trials|studies)\b", re.I)
_COMPARE_RE = re.compile(r"\b([A-Za-z][\w\-'/ ]*?)\s+(?:vs\.?|versus|compared (?:to|with)|and)\s+(?:the\s+)?([A-Za-z][\w\-'/ ]*?)" + _TERMINATORS, re.I)
_SPONSORED_RE = re.compile(r"(?:sponsored|funded|run) by\s+([A-Za-z][\w\-&'. ]*?)" + _TERMINATORS, re.I)
_IN_COUNTRY_RE = re.compile(r"\bin\s+(?:the\s+)?([A-Z][A-Za-z'. ]{2,40}?)" + _TERMINATORS)

_FUNCTION_WORDS = {"how", "many", "which", "what", "are", "is", "the", "most", "common", "number", "of", "for", "recruiting", "completed", "all", "show", "compare", "me", "a", "an", "distribution", "distributed", "across", "phase", "phases", "sponsor", "sponsors", "drug", "drugs", "condition", "conditions", "network", "top", "per", "by", "and", "vs", "versus", "with", "in", "on", "to", "from", "since", "over", "time", "year", "years", "each", "every", "intervention", "interventions", "types", "type", "started", "start", "largest", "biggest", "active", "ongoing", "new", "recent", "interventional", "observational", "industry", "academic", "pediatric", "adult", "changed", "change", "changes", "evolved", "grown", "vary", "varied", "varies", "distributed", "distribution", "began"}


# ----------------------------------------------------------------------------- helpers
def _clean_entity(text: str) -> str | None:
    text = re.sub(r"\s+", " ", text).strip(" -'/")
    text = re.sub(r"\b(?:early )?phase\s+(?:\d|i{1,3}|iv)\b", " ", text, flags=re.I)
    words = text.split()
    while words and words[0].lower() in _LEADING_FUNCTION_WORDS:
        words.pop(0)
    while words and words[-1].lower() in {"trials", "trial", "studies", "study", "the", "a", "an", "of", "for", "in"}:
        words.pop()
    text = " ".join(words).strip(" -'/:")
    if not text or text.lower() in _ENTITY_STOP or len(text) < 2:
        return None
    if all(w.lower() in _FUNCTION_WORDS for w in text.split()):
        return None
    if re.fullmatch(r"\d{4}", text):
        return None
    return text


_LEADING_FUNCTION_WORDS = _FUNCTION_WORDS | {"trials", "trial", "studies", "study", "clinical", "involving", "about", "using", "between", "investigating", "testing", "evaluating", "has", "have", "does", "do", "did", "been", "there", "where", "when", "who", "that", "which", "this", "these", "those", "that", "patients", "people", "subjects", "it", "its", "their", "those", "then", "than", "as", "at", "or", "but", "into", "through", "during", "within", "after", "before", "only", "also", "studied", "tested", "run", "being", "being", "was", "were", "be", "been", "can", "could", "would", "should", "will", "please", "list", "give", "find", "plot", "draw", "chart", "visualize", "visualise", "display", "total", "count", "counts", "see", "look"}


def _entity_score(entity: str, query: str) -> int:
    """Rank candidate entities: typed (drug/condition-looking) names beat generic phrases."""
    score = 0
    if _CONDITION_HINT.search(entity) or _DRUG_HINT.search(entity):
        score += 2
    if re.search(r"\b(?:for|involving|with|using|of)\s+(?:the\s+|this\s+)?(?:drug\s+|condition\s+|disease\s+)?" + re.escape(entity), query, re.I):
        score += 1
    if len(entity.split()) > 4:
        score -= 1
    if country_codes(entity)[0] and len(entity) > 3:
        score -= 1  # countries are filters, not subjects
    return score


def _best_entity(q: str) -> str | None:
    candidates: list[str] = []
    for m in _ENTITY_RE.finditer(q):
        if (e := _clean_entity(m.group(1))) and e not in candidates:
            candidates.append(e)
    for m in _PREFIX_ENTITY_RE.finditer(q):
        if (e := _clean_entity(m.group(1))) and e not in candidates:
            candidates.append(e)
    if not candidates:
        return None
    return max(candidates, key=lambda e: (_entity_score(e, q), -candidates.index(e)))


def _typed_filters(entity: str, query: str, notes: list[str]) -> CohortFilters:
    """Decide whether an entity is a drug, a condition, a country, or unknown, and build filters."""
    q = query.lower()
    ent = entity.lower()
    before = q.split(ent, 1)[0] if ent in q else ""
    immediately_before = re.search(r"(\w+)\s+(?:the\s+|this\s+)?$", before)
    prev_word = immediately_before.group(1) if immediately_before else ""
    if _CONDITION_HINT.search(ent) or prev_word in {"condition", "disease", "indication"} or before.endswith("patients with "):
        return CohortFilters(condition=entity)
    if _DRUG_HINT.search(ent) or prev_word in {"drug", "intervention", "treatment", "compound", "involving", "using", "testing", "evaluating"}:
        return CohortFilters(intervention=entity)
    if country_codes(entity)[0] and len(entity) > 3 and prev_word == "in":
        return CohortFilters(country=entity)
    notes.append(f"'{entity}' matched as free text across all fields; pass drug_name or condition to pin it.")
    return CohortFilters(free_text=entity)


def _extract_years(q: str, today: date) -> tuple[int | None, int | None]:
    if m := re.search(r"(?:from|between)\s+(\d{4})\s+(?:to|and|-|–|until|through)\s+(\d{4})", q):
        return int(m.group(1)), int(m.group(2))
    lo = hi = None
    if m := re.search(r"\bsince\s+(\d{4})", q):
        lo = int(m.group(1))
    if m := re.search(r"\bafter\s+(\d{4})", q):
        lo = int(m.group(1)) + 1
    if m := re.search(r"\b(?:before|until|through|up to)\s+(\d{4})", q):
        hi = int(m.group(1)) - (1 if "before" in m.group(0) else 0)
    if m := re.search(r"\b(?:last|past)\s+(\d+)\s+years?", q):
        lo = today.year - int(m.group(1))
    elif re.search(r"\b(?:last|past)\s+decade", q):
        lo = today.year - 10
    if lo is None and hi is None and (m := re.search(r"\bin\s+(\d{4})\b", q)):
        lo = hi = int(m.group(1))
    return lo, hi


def _detect_kind(q: str) -> AnalysisKind:
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(q):
            return kind
    return AnalysisKind.DISTRIBUTION


def _detect_dimension(q: str, exclude: set[Dimension]) -> Dimension | None:
    for dim, pattern in _DIMENSION_PATTERNS:
        if dim not in exclude and pattern.search(q):
            return dim
    return None


def _detect_numeric(q: str, default: NumericField) -> NumericField:
    if re.search(r"duration|how long", q, re.I):
        return NumericField.DURATION_MONTHS
    if re.search(r"\bsites?\b|locations?\b", q, re.I):
        return NumericField.SITE_COUNT
    if re.search(r"number of countries|countries per", q, re.I):
        return NumericField.COUNTRY_COUNT
    if re.search(r"enrollment|sample size|participants|trial size|how (?:large|big)", q, re.I):
        return NumericField.ENROLLMENT
    if re.search(r"start year|\byear\b", q, re.I):
        return NumericField.START_YEAR
    return default


# ----------------------------------------------------------------------------- planner
class RuleBasedPlanner:
    async def plan(self, query: str, today: str, context: dict | None = None) -> PlanResult:
        return plan_with_rules(query, date.fromisoformat(today))


def plan_with_rules(query: str, today: date) -> PlanResult:
    notes: list[str] = []
    q = query.strip()
    kind = _detect_kind(q)

    # --- entities / cohorts
    cohorts: list[Cohort] = []
    if kind == AnalysisKind.COMPARISON:
        for m in _COMPARE_RE.finditer(q):
            a, b = _clean_entity(m.group(1)), _clean_entity(m.group(2))
            if a and b:
                cohorts = [Cohort(label=a, filters=_typed_filters(a, q, notes)), Cohort(label=b, filters=_typed_filters(b, q, notes))]
                break
    if not cohorts:
        entity = _best_entity(q)
        filters = _typed_filters(entity, q, notes) if entity else CohortFilters()
        label = entity or "All trials"
        if kind == AnalysisKind.COMPARISON:
            kind = AnalysisKind.DISTRIBUTION
            notes.append("Could not identify two things to compare; showing a single distribution instead.")
        cohorts = [Cohort(label=label, filters=filters)]

    # --- shared filters
    shared = CohortFilters()
    if m := _SPONSORED_RE.search(q):
        if s := _clean_entity(m.group(1)):
            shared.sponsor = s
    for m in _IN_COUNTRY_RE.finditer(q):
        cand = m.group(1).strip()
        if country_codes(cand)[0] and not any(c.filters.country == cand for c in cohorts):
            shared.country = cand
            break
    lo, hi = _extract_years(q, today)
    shared.start_year_from, shared.start_year_to = lo, hi

    dimension = None
    exclude: set[Dimension] = set()
    if kind in (AnalysisKind.DISTRIBUTION, AnalysisKind.COMPARISON):
        dimension = _detect_dimension(q, exclude) or Dimension.PHASE
        if dimension == Dimension.PHASE and not re.search(r"\bphases?\b", q, re.I):
            notes.append("No grouping field named; defaulting to phase.")

    for pat, statuses in _STATUS_PATTERNS:
        if pat.search(q) and dimension != Dimension.STATUS:
            shared.statuses = statuses
            break

    if dimension != Dimension.PHASE and (m := re.search(r"\b(?:early )?phase\s+(1|2|3|4|i{1,3}|iv)\b", q, re.I)):
        phase = _PHASE_WORDS[m.group(1).lower()]
        shared.phases = [Phase.EARLY_PHASE1] if m.group(0).lower().startswith("early") else [phase]

    for cohort in cohorts:
        for key, value in shared.model_dump(exclude_none=True).items():
            if getattr(cohort.filters, key) is None:
                setattr(cohort.filters, key, value)

    # --- measure, granularity, top_n
    measure = Measure.TRIAL_COUNT
    if re.search(r"total enrollment|participants enrolled|number of participants|how many (?:patients|participants)", q, re.I):
        measure = Measure.ENROLLMENT_TOTAL
    elif re.search(r"(?:average|mean) enrollment", q, re.I):
        measure = Measure.ENROLLMENT_MEAN
    elif re.search(r"median enrollment", q, re.I):
        measure = Measure.ENROLLMENT_MEDIAN

    granularity = TimeGranularity.YEAR
    if re.search(r"\bmonth", q, re.I):
        granularity = TimeGranularity.MONTH
    elif re.search(r"\bquarter", q, re.I):
        granularity = TimeGranularity.QUARTER

    top_n = int(m.group(1)) if (m := re.search(r"\btop\s+(\d{1,3})\b", q, re.I)) else None
    if top_n is None and dimension in (Dimension.SPONSOR, Dimension.COLLABORATOR, Dimension.INTERVENTION, Dimension.CONDITION, Dimension.COUNTRY):
        top_n = 15
    if top_n is None and kind == AnalysisKind.GEOGRAPHIC:
        top_n = 25

    # --- type-specific specs
    network = scatter = histogram = None
    if kind == AnalysisKind.RELATIONSHIP:
        found: list[tuple[int, EntityType]] = []
        for t, pat in _ENTITY_WORDS:
            if (m := pat.search(q)) and all(t != ft for _, ft in found):
                found.append((m.start(), t))
        found_types = [t for _, t in sorted(found, key=lambda x: x[0])]
        if re.search(r"drugs?\s*(?:↔|<->|and|with|vs\.?|versus)\s*drugs?|co-?occur|combination", q, re.I) and EntityType.INTERVENTION in found_types:
            node_types = [EntityType.INTERVENTION]
        elif len(found_types) >= 2:
            node_types = found_types[:2]
        elif len(found_types) == 1:
            node_types = [found_types[0]] if found_types[0] == EntityType.INTERVENTION else [found_types[0], EntityType.INTERVENTION]
        else:
            node_types = [EntityType.SPONSOR, EntityType.INTERVENTION]
            notes.append("No entity types named for the network; defaulting to sponsor ↔ drug.")
        network = NetworkSpec(node_types=node_types)
    elif kind == AnalysisKind.SCATTER:
        if m := re.search(r"([\w ]+?)\s+(?:vs\.?|versus|against)\s+([\w ]+)", q, re.I):
            y = _detect_numeric(m.group(1), NumericField.ENROLLMENT)
            x = _detect_numeric(m.group(2), NumericField.START_YEAR)
        else:
            y, x = NumericField.ENROLLMENT, NumericField.START_YEAR
        if x == y:
            x = NumericField.START_YEAR if y != NumericField.START_YEAR else NumericField.ENROLLMENT
        color = Dimension.PHASE if re.search(r"\bphase", q, re.I) else None
        scatter = ScatterSpec(x=x, y=y, color=color)
    elif kind == AnalysisKind.HISTOGRAM:
        histogram = HistogramSpec(field=_detect_numeric(q, NumericField.ENROLLMENT))

    interpretation = _describe(kind, dimension, cohorts, measure, granularity, network, scatter, histogram)
    plan = QueryPlan(
        analysis=kind,
        cohorts=cohorts,
        dimension=dimension,
        measure=measure,
        granularity=granularity,
        network=network,
        scatter=scatter,
        histogram=histogram,
        top_n=top_n,
        sort=SortOrder.NATURAL,
        interpretation=interpretation,
    )
    return PlanResult(plan=validate_plan(plan), planner_used="rules", notes=notes)


def _describe(kind, dimension, cohorts, measure, granularity, network, scatter, histogram) -> str:
    subject = "; ".join(f"{c.label}: {c.filters.describe() or 'all trials'}" for c in cohorts)
    if kind == AnalysisKind.TIME_TREND:
        return f"Counting trials by start date per {granularity.value} for {subject}."
    if kind == AnalysisKind.DISTRIBUTION:
        return f"Grouping trials by {dimension.value.replace('_', ' ')} ({measure.value.replace('_', ' ')}) for {subject}."
    if kind == AnalysisKind.COMPARISON:
        return f"Comparing {dimension.value.replace('_', ' ')} across cohorts — {subject}."
    if kind == AnalysisKind.GEOGRAPHIC:
        return f"Counting trials per country (each trial counted once per country it has a site in) for {subject}."
    if kind == AnalysisKind.RELATIONSHIP:
        return f"Building a {' ↔ '.join(t.value for t in network.node_types)} co-occurrence network for {subject}."
    if kind == AnalysisKind.SCATTER:
        return f"Plotting {scatter.y.value} against {scatter.x.value} per trial for {subject}."
    return f"Histogram of {histogram.field.value} across trials for {subject}."
