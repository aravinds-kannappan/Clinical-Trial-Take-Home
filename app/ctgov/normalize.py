"""Turn raw ClinicalTrials.gov study JSON into a flat, typed ``TrialRecord``.

Real-world data is messy: phases come as lists, dates are partial, countries repeat once
per site, intervention names are free text. All of that is handled here, once, so that
aggregation code works on clean values. Each normalized value keeps the JSON path it came
from so citations can quote the exact source field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.schemas.common import RAW_PHASE_TO_LABEL, Phase

CTGOV_STUDY_URL = "https://clinicaltrials.gov/study/{nct_id}"

P_ID = "protocolSection.identificationModule"
P_STATUS = "protocolSection.statusModule"
P_SPONSOR = "protocolSection.sponsorCollaboratorsModule"
P_COND = "protocolSection.conditionsModule"
P_DESIGN = "protocolSection.designModule"
P_ARMS = "protocolSection.armsInterventionsModule"
P_LOC = "protocolSection.contactsLocationsModule"
P_ELIG = "protocolSection.eligibilityModule"
D_COND = "derivedSection.conditionBrowseModule"
D_INTR = "derivedSection.interventionBrowseModule"


@dataclass(frozen=True)
class Sourced:
    """A value together with the JSON path it was read from."""

    value: str
    path: str


@dataclass
class Intervention:
    name: str
    type: str | None
    index: int

    @property
    def name_path(self) -> str:
        return f"{P_ARMS}.interventions[{self.index}].name"

    @property
    def type_path(self) -> str:
        return f"{P_ARMS}.interventions[{self.index}].type"


@dataclass
class TrialRecord:
    nct_id: str
    title: str
    status: str | None
    phases_raw: list[str]
    phase_label: Phase
    study_type: str | None
    start_date: str | None
    primary_completion_date: str | None
    completion_date: str | None
    first_posted_date: str | None
    lead_sponsor: str | None
    sponsor_class: str | None
    collaborators: list[Sourced]
    conditions: list[Sourced]
    condition_mesh: list[Sourced]
    interventions: list[Intervention]
    intervention_mesh: list[Sourced]
    locations_country: list[Sourced]  # one per site, in API order (used for citations)
    countries: list[str]  # de-duplicated, in first-seen order
    site_count: int
    investigators: list[Sourced]
    enrollment: int | None
    enrollment_type: str | None
    allocation: str | None
    primary_purpose: str | None
    sex: str | None
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return CTGOV_STUDY_URL.format(nct_id=self.nct_id)

    @property
    def start_year(self) -> int | None:
        return parse_year(self.start_date)

    def date_for(self, field_name: str) -> str | None:
        return {
            "start_date": self.start_date,
            "primary_completion_date": self.primary_completion_date,
            "completion_date": self.completion_date,
            "first_posted_date": self.first_posted_date,
        }.get(field_name)

    @property
    def duration_months(self) -> float | None:
        start = parse_date(self.start_date)
        end = parse_date(self.primary_completion_date) or parse_date(self.completion_date)
        if not start or not end or end < start:
            return None
        return round((end.year - start.year) * 12 + (end.month - start.month) + (end.day - start.day) / 30.0, 1)


# ----------------------------------------------------------------------------- helpers
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    m = _DATE_RE.match(value.strip())
    return int(m.group(1)) if m else None


def parse_date(value: str | None) -> date | None:
    """Parse '2024', '2024-07' or '2024-07-03'. Partial dates resolve to the first day."""
    if not value:
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def phase_label(phases: list[str]) -> Phase:
    """Map the API's phase list to a single label, including combined phases."""
    tokens = [p for p in phases if p]
    if not tokens:
        return Phase.NOT_REPORTED
    key = set(tokens)
    if key == {"PHASE1", "PHASE2"}:
        return Phase.PHASE1_PHASE2
    if key == {"PHASE2", "PHASE3"}:
        return Phase.PHASE2_PHASE3
    if len(tokens) == 1:
        return RAW_PHASE_TO_LABEL.get(tokens[0], Phase.NOT_REPORTED)
    # Unusual combination: fall back to the highest phase present.
    for raw in ("PHASE4", "PHASE3", "PHASE2", "PHASE1", "EARLY_PHASE1", "NA"):
        if raw in key:
            return RAW_PHASE_TO_LABEL[raw]
    return Phase.NOT_REPORTED


_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Case-insensitive, whitespace-collapsed key for grouping free-text names."""
    return _WS.sub(" ", name.strip().casefold())


_PLACEBO = re.compile(r"\b(placebo|sham|vehicle|saline|no intervention|standard of care|usual care|best supportive care)\b", re.I)


def is_placebo_like(name: str) -> bool:
    return bool(_PLACEBO.search(name))


# ----------------------------------------------------------------------------- parser
def parse_study(raw: dict) -> TrialRecord:
    proto = raw.get("protocolSection", {}) or {}
    derived = raw.get("derivedSection", {}) or {}
    ident = proto.get("identificationModule", {}) or {}
    status = proto.get("statusModule", {}) or {}
    spons = proto.get("sponsorCollaboratorsModule", {}) or {}
    cond = proto.get("conditionsModule", {}) or {}
    design = proto.get("designModule", {}) or {}
    arms = proto.get("armsInterventionsModule", {}) or {}
    loc = proto.get("contactsLocationsModule", {}) or {}
    elig = proto.get("eligibilityModule", {}) or {}

    phases_raw = list(design.get("phases") or [])
    lead = spons.get("leadSponsor", {}) or {}

    collaborators = [
        Sourced(c["name"], f"{P_SPONSOR}.collaborators[{i}].name")
        for i, c in enumerate(spons.get("collaborators") or [])
        if c.get("name")
    ]
    conditions = [Sourced(c, f"{P_COND}.conditions[{i}]") for i, c in enumerate(cond.get("conditions") or []) if c]
    condition_mesh = [
        Sourced(m["term"], f"{D_COND}.meshes[{i}].term")
        for i, m in enumerate((derived.get("conditionBrowseModule") or {}).get("meshes") or [])
        if m.get("term")
    ]
    interventions = [
        Intervention(name=it["name"], type=it.get("type"), index=i)
        for i, it in enumerate(arms.get("interventions") or [])
        if it.get("name")
    ]
    intervention_mesh = [
        Sourced(m["term"], f"{D_INTR}.meshes[{i}].term")
        for i, m in enumerate((derived.get("interventionBrowseModule") or {}).get("meshes") or [])
        if m.get("term")
    ]
    locations = loc.get("locations") or []
    locations_country = [
        Sourced(l["country"], f"{P_LOC}.locations[{i}].country") for i, l in enumerate(locations) if l.get("country")
    ]
    countries = list(dict.fromkeys(s.value for s in locations_country))
    investigators = [
        Sourced(o["name"], f"{P_LOC}.overallOfficials[{i}].name")
        for i, o in enumerate(loc.get("overallOfficials") or [])
        if o.get("name")
    ]
    enrollment_info = design.get("enrollmentInfo", {}) or {}
    design_info = design.get("designInfo", {}) or {}

    return TrialRecord(
        nct_id=ident.get("nctId", ""),
        title=ident.get("briefTitle", "") or "",
        status=status.get("overallStatus"),
        phases_raw=phases_raw,
        phase_label=phase_label(phases_raw),
        study_type=design.get("studyType"),
        start_date=(status.get("startDateStruct") or {}).get("date"),
        primary_completion_date=(status.get("primaryCompletionDateStruct") or {}).get("date"),
        completion_date=(status.get("completionDateStruct") or {}).get("date"),
        first_posted_date=(status.get("studyFirstPostDateStruct") or {}).get("date"),
        lead_sponsor=lead.get("name"),
        sponsor_class=lead.get("class"),
        collaborators=collaborators,
        conditions=conditions,
        condition_mesh=condition_mesh,
        interventions=interventions,
        intervention_mesh=intervention_mesh,
        locations_country=locations_country,
        countries=countries,
        site_count=len(locations),
        investigators=investigators,
        enrollment=enrollment_info.get("count"),
        enrollment_type=enrollment_info.get("type"),
        allocation=design_info.get("allocation"),
        primary_purpose=design_info.get("primaryPurpose"),
        sex=elig.get("sex"),
    )
