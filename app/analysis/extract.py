"""Extract grouping values, numeric values, and entities from a TrialRecord *with evidence*.

Every extracted value is paired with the exact API field path and raw value that supports
it, which is what makes deep citations possible without a second pass over the data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.ctgov.normalize import (
    P_DESIGN,
    P_ELIG,
    P_SPONSOR,
    P_STATUS,
    TrialRecord,
    is_placebo_like,
    normalize_name,
)
from app.schemas.common import Dimension, EntityType, NumericField
from app.schemas.response import Evidence

# Intervention types that count as "drugs" for drug networks and drug grouping.
DRUG_LIKE_TYPES = {"DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT", "DIETARY_SUPPLEMENT", None}


@dataclass(frozen=True)
class Extracted:
    key: str  # grouping key (normalized)
    label: str  # display label
    evidence: Evidence


def _single(key: str | None, path: str, missing_label: str = "Not reported") -> list[Extracted]:
    if key is None or key == "":
        return [Extracted(missing_label, missing_label, Evidence(field=path, excerpt="(not reported)"))]
    return [Extracted(str(key), str(key), Evidence(field=path, excerpt=str(key)))]


def dimension_values(record: TrialRecord, dim: Dimension) -> list[Extracted]:
    """Return the bucket(s) a trial belongs to for ``dim``. Multi-valued dims return several."""
    if dim == Dimension.PHASE:
        excerpt = json.dumps(record.phases_raw) if record.phases_raw else "(not reported)"
        return [Extracted(record.phase_label.value, record.phase_label.value, Evidence(field=f"{P_DESIGN}.phases", excerpt=excerpt))]
    if dim == Dimension.STATUS:
        return _single(record.status, f"{P_STATUS}.overallStatus")
    if dim == Dimension.SPONSOR_CLASS:
        return _single(record.sponsor_class, f"{P_SPONSOR}.leadSponsor.class")
    if dim == Dimension.SPONSOR:
        return _single(record.lead_sponsor, f"{P_SPONSOR}.leadSponsor.name")
    if dim == Dimension.COLLABORATOR:
        return _dedupe([Extracted(normalize_name(c.value), c.value, Evidence(field=c.path, excerpt=c.value)) for c in record.collaborators])
    if dim == Dimension.INTERVENTION_TYPE:
        out = []
        for it in record.interventions:
            label = it.type or "Not reported"
            out.append(Extracted(label, label, Evidence(field=it.type_path, excerpt=it.type or "(not reported)")))
        return _dedupe(out) or _single(None, f"{P_DESIGN}.interventions")
    if dim == Dimension.INTERVENTION:
        return _dedupe(
            [
                Extracted(normalize_name(it.name), it.name.strip(), Evidence(field=it.name_path, excerpt=it.name))
                for it in record.interventions
                if not is_placebo_like(it.name)
            ]
        )
    if dim == Dimension.CONDITION:
        source = record.condition_mesh or record.conditions
        return _dedupe([Extracted(normalize_name(c.value), c.value, Evidence(field=c.path, excerpt=c.value)) for c in source])
    if dim == Dimension.COUNTRY:
        seen: dict[str, Extracted] = {}
        for s in record.locations_country:
            if s.value not in seen:
                seen[s.value] = Extracted(s.value, s.value, Evidence(field=s.path, excerpt=s.value))
        return list(seen.values())
    if dim == Dimension.STUDY_TYPE:
        return _single(record.study_type, f"{P_DESIGN}.studyType")
    if dim == Dimension.PRIMARY_PURPOSE:
        return _single(record.primary_purpose, f"{P_DESIGN}.designInfo.primaryPurpose")
    if dim == Dimension.ALLOCATION:
        return _single(record.allocation, f"{P_DESIGN}.designInfo.allocation")
    if dim == Dimension.SEX:
        return _single(record.sex, f"{P_ELIG}.sex")
    if dim == Dimension.START_YEAR:
        year = record.start_year
        return _single(str(year) if year else None, f"{P_STATUS}.startDateStruct.date") if year else [
            Extracted("Not reported", "Not reported", Evidence(field=f"{P_STATUS}.startDateStruct.date", excerpt="(not reported)"))
        ]
    raise ValueError(f"Unsupported dimension: {dim}")


def _dedupe(items: list[Extracted]) -> list[Extracted]:
    seen: dict[str, Extracted] = {}
    for item in items:
        seen.setdefault(item.key, item)
    return list(seen.values())


def numeric_value(record: TrialRecord, field_name: NumericField) -> tuple[float | None, Evidence]:
    """Return (value, evidence). Value is None when the trial lacks the data."""
    if field_name == NumericField.ENROLLMENT:
        v = record.enrollment
        return (float(v) if v is not None else None, Evidence(field=f"{P_DESIGN}.enrollmentInfo.count", excerpt=str(v) if v is not None else "(not reported)"))
    if field_name == NumericField.START_YEAR:
        y = record.start_year
        return (float(y) if y else None, Evidence(field=f"{P_STATUS}.startDateStruct.date", excerpt=record.start_date or "(not reported)"))
    if field_name == NumericField.DURATION_MONTHS:
        d = record.duration_months
        return (d, Evidence(field=f"{P_STATUS}.startDateStruct.date → primaryCompletionDateStruct.date", excerpt=f"{record.start_date} → {record.primary_completion_date or record.completion_date}"))
    if field_name == NumericField.SITE_COUNT:
        return (float(record.site_count), Evidence(field="protocolSection.contactsLocationsModule.locations", excerpt=f"{record.site_count} locations"))
    if field_name == NumericField.COUNTRY_COUNT:
        return (float(len(record.countries)), Evidence(field="protocolSection.contactsLocationsModule.locations[*].country", excerpt=", ".join(record.countries) or "(none)"))
    if field_name == NumericField.INTERVENTION_COUNT:
        return (float(len(record.interventions)), Evidence(field="protocolSection.armsInterventionsModule.interventions", excerpt=f"{len(record.interventions)} interventions"))
    if field_name == NumericField.CONDITION_COUNT:
        return (float(len(record.conditions)), Evidence(field="protocolSection.conditionsModule.conditions", excerpt=f"{len(record.conditions)} conditions"))
    raise ValueError(f"Unsupported numeric field: {field_name}")


def entities(record: TrialRecord, entity_type: EntityType) -> list[Extracted]:
    """Entities of one type mentioned by a trial (used for network graphs)."""
    if entity_type == EntityType.SPONSOR:
        return _single(record.lead_sponsor, f"{P_SPONSOR}.leadSponsor.name") if record.lead_sponsor else []
    if entity_type == EntityType.COLLABORATOR:
        return dimension_values(record, Dimension.COLLABORATOR)
    if entity_type == EntityType.INTERVENTION:
        return _dedupe(
            [
                Extracted(normalize_name(it.name), it.name.strip(), Evidence(field=it.name_path, excerpt=it.name))
                for it in record.interventions
                if it.type in DRUG_LIKE_TYPES and not is_placebo_like(it.name)
            ]
        )
    if entity_type == EntityType.CONDITION:
        return dimension_values(record, Dimension.CONDITION)
    if entity_type == EntityType.COUNTRY:
        return dimension_values(record, Dimension.COUNTRY)
    if entity_type == EntityType.INVESTIGATOR:
        return _dedupe([Extracted(normalize_name(s.value), s.value, Evidence(field=s.path, excerpt=s.value)) for s in record.investigators])
    raise ValueError(f"Unsupported entity type: {entity_type}")
