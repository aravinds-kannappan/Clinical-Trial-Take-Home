"""Enumerations shared by the request, plan, and response schemas.

Values mirror ClinicalTrials.gov API v2 vocabularies where one exists, so a frontend
can rely on a closed set of strings.
"""

from enum import Enum


class Phase(str, Enum):
    """Human-readable phase labels. Multi-phase studies get a combined label."""

    EARLY_PHASE1 = "Early Phase 1"
    PHASE1 = "Phase 1"
    PHASE1_PHASE2 = "Phase 1/Phase 2"
    PHASE2 = "Phase 2"
    PHASE2_PHASE3 = "Phase 2/Phase 3"
    PHASE3 = "Phase 3"
    PHASE4 = "Phase 4"
    NOT_APPLICABLE = "Not Applicable"
    NOT_REPORTED = "Not Reported"


# Raw API tokens -> label. The API returns a list; combinations are handled in normalize.py.
RAW_PHASE_TO_LABEL = {
    "EARLY_PHASE1": Phase.EARLY_PHASE1,
    "PHASE1": Phase.PHASE1,
    "PHASE2": Phase.PHASE2,
    "PHASE3": Phase.PHASE3,
    "PHASE4": Phase.PHASE4,
    "NA": Phase.NOT_APPLICABLE,
}

# Label -> raw tokens used when *filtering* by phase through the API.
PHASE_LABEL_TO_RAW: dict[Phase, list[str]] = {
    Phase.EARLY_PHASE1: ["EARLY_PHASE1"],
    Phase.PHASE1: ["PHASE1"],
    Phase.PHASE1_PHASE2: ["PHASE1", "PHASE2"],
    Phase.PHASE2: ["PHASE2"],
    Phase.PHASE2_PHASE3: ["PHASE2", "PHASE3"],
    Phase.PHASE3: ["PHASE3"],
    Phase.PHASE4: ["PHASE4"],
    Phase.NOT_APPLICABLE: ["NA"],
}

PHASE_ORDER = [p.value for p in Phase]


class OverallStatus(str, Enum):
    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"


class StudyType(str, Enum):
    INTERVENTIONAL = "INTERVENTIONAL"
    OBSERVATIONAL = "OBSERVATIONAL"
    EXPANDED_ACCESS = "EXPANDED_ACCESS"


class SponsorClass(str, Enum):
    INDUSTRY = "INDUSTRY"
    NIH = "NIH"
    FED = "FED"
    OTHER_GOV = "OTHER_GOV"
    INDIV = "INDIV"
    NETWORK = "NETWORK"
    AMBIG = "AMBIG"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class AnalysisKind(str, Enum):
    """The question class. Each kind maps to exactly one visualization type."""

    TIME_TREND = "time_trend"
    DISTRIBUTION = "distribution"
    COMPARISON = "comparison"
    GEOGRAPHIC = "geographic"
    RELATIONSHIP = "relationship"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"


class VisualizationType(str, Enum):
    BAR_CHART = "bar_chart"
    GROUPED_BAR_CHART = "grouped_bar_chart"
    TIME_SERIES = "time_series"
    CHOROPLETH_MAP = "choropleth_map"
    NETWORK_GRAPH = "network_graph"
    SCATTER_PLOT = "scatter_plot"
    HISTOGRAM = "histogram"


ANALYSIS_TO_VIZ: dict[AnalysisKind, VisualizationType] = {
    AnalysisKind.TIME_TREND: VisualizationType.TIME_SERIES,
    AnalysisKind.DISTRIBUTION: VisualizationType.BAR_CHART,
    AnalysisKind.COMPARISON: VisualizationType.GROUPED_BAR_CHART,
    AnalysisKind.GEOGRAPHIC: VisualizationType.CHOROPLETH_MAP,
    AnalysisKind.RELATIONSHIP: VisualizationType.NETWORK_GRAPH,
    AnalysisKind.SCATTER: VisualizationType.SCATTER_PLOT,
    AnalysisKind.HISTOGRAM: VisualizationType.HISTOGRAM,
}


class Dimension(str, Enum):
    """Categorical fields a trial can be grouped by."""

    PHASE = "phase"
    STATUS = "status"
    SPONSOR_CLASS = "sponsor_class"
    SPONSOR = "sponsor"
    COLLABORATOR = "collaborator"
    INTERVENTION_TYPE = "intervention_type"
    INTERVENTION = "intervention"
    CONDITION = "condition"
    COUNTRY = "country"
    STUDY_TYPE = "study_type"
    PRIMARY_PURPOSE = "primary_purpose"
    ALLOCATION = "allocation"
    SEX = "sex"
    START_YEAR = "start_year"


# Dimensions where one trial can fall into several buckets (counts then exceed trial totals).
MULTI_VALUED_DIMENSIONS = {
    Dimension.COLLABORATOR,
    Dimension.INTERVENTION_TYPE,
    Dimension.INTERVENTION,
    Dimension.CONDITION,
    Dimension.COUNTRY,
}

DIMENSION_LABELS = {
    Dimension.PHASE: "Phase",
    Dimension.STATUS: "Overall status",
    Dimension.SPONSOR_CLASS: "Sponsor class",
    Dimension.SPONSOR: "Lead sponsor",
    Dimension.COLLABORATOR: "Collaborator",
    Dimension.INTERVENTION_TYPE: "Intervention type",
    Dimension.INTERVENTION: "Intervention",
    Dimension.CONDITION: "Condition",
    Dimension.COUNTRY: "Country",
    Dimension.STUDY_TYPE: "Study type",
    Dimension.PRIMARY_PURPOSE: "Primary purpose",
    Dimension.ALLOCATION: "Allocation",
    Dimension.SEX: "Sex eligibility",
    Dimension.START_YEAR: "Start year",
}


class Measure(str, Enum):
    TRIAL_COUNT = "trial_count"
    ENROLLMENT_TOTAL = "enrollment_total"
    ENROLLMENT_MEAN = "enrollment_mean"
    ENROLLMENT_MEDIAN = "enrollment_median"


MEASURE_LABELS = {
    Measure.TRIAL_COUNT: "Number of trials",
    Measure.ENROLLMENT_TOTAL: "Total enrollment",
    Measure.ENROLLMENT_MEAN: "Mean enrollment",
    Measure.ENROLLMENT_MEDIAN: "Median enrollment",
}

MEASURE_UNITS = {
    Measure.TRIAL_COUNT: "trials",
    Measure.ENROLLMENT_TOTAL: "participants",
    Measure.ENROLLMENT_MEAN: "participants per trial",
    Measure.ENROLLMENT_MEDIAN: "participants per trial",
}


class TimeGranularity(str, Enum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"


class TimeField(str, Enum):
    START_DATE = "start_date"
    PRIMARY_COMPLETION_DATE = "primary_completion_date"
    COMPLETION_DATE = "completion_date"
    FIRST_POSTED_DATE = "first_posted_date"


TIME_FIELD_LABELS = {
    TimeField.START_DATE: "Start date",
    TimeField.PRIMARY_COMPLETION_DATE: "Primary completion date",
    TimeField.COMPLETION_DATE: "Completion date",
    TimeField.FIRST_POSTED_DATE: "First posted date",
}


class NumericField(str, Enum):
    ENROLLMENT = "enrollment"
    START_YEAR = "start_year"
    DURATION_MONTHS = "duration_months"
    SITE_COUNT = "site_count"
    COUNTRY_COUNT = "country_count"
    INTERVENTION_COUNT = "intervention_count"
    CONDITION_COUNT = "condition_count"


NUMERIC_FIELD_LABELS = {
    NumericField.ENROLLMENT: "Enrollment (participants)",
    NumericField.START_YEAR: "Start year",
    NumericField.DURATION_MONTHS: "Planned duration (months)",
    NumericField.SITE_COUNT: "Number of sites",
    NumericField.COUNTRY_COUNT: "Number of countries",
    NumericField.INTERVENTION_COUNT: "Number of interventions",
    NumericField.CONDITION_COUNT: "Number of conditions",
}


class EntityType(str, Enum):
    """Node types available for network graphs."""

    SPONSOR = "sponsor"
    COLLABORATOR = "collaborator"
    INTERVENTION = "intervention"
    CONDITION = "condition"
    COUNTRY = "country"
    INVESTIGATOR = "investigator"


ENTITY_LABELS = {
    EntityType.SPONSOR: "Sponsor",
    EntityType.COLLABORATOR: "Collaborator",
    EntityType.INTERVENTION: "Drug / intervention",
    EntityType.CONDITION: "Condition",
    EntityType.COUNTRY: "Country",
    EntityType.INVESTIGATOR: "Investigator",
}


class SortOrder(str, Enum):
    DESC = "desc"
    ASC = "asc"
    NATURAL = "natural"  # e.g. phase order, chronological


class PlannerChoice(str, Enum):
    AUTO = "auto"
    LLM = "llm"
    RULES = "rules"
