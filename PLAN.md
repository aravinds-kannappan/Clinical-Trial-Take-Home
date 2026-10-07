# Build Plan: ClinicalTrials.gov Query-to-Visualization Agent

This file is the plan that was written before implementation started. It follows the
assignment sections in order. The README documents what was actually built.

## 0. Environment facts that shape the plan

- ClinicalTrials.gov Data API v2 (`/api/v2/studies`) verified live: supports
  `query.intr`, `query.cond`, `query.spons`, `query.locn`, `query.term`,
  `filter.overallStatus`, `filter.advanced` (e.g. `AREA[StartDate]RANGE[2015-01-01,MAX]`),
  `fields=` projection, `pageSize` up to 1000, `nextPageToken` pagination, `countTotal`.
- Errors come back as plain-text bodies (e.g. "Unknown area name"), not JSON.
- Phases are a list (`["PHASE1","PHASE2"]`), dates can be partial (`"2027-12"`),
  locations repeat countries per site, intervention names are free text.
- No Anthropic credentials on the build machine, so the LLM planner must be optional and
  the service must be fully functional without it.

## 1. Problem decomposition (Section 1 of the brief)

Four stages, each a separate module with one responsibility:

1. **Interpret** – natural-language query (+ optional structured fields) → `QueryPlan`.
2. **Retrieve** – `QueryPlan.filters` → ClinicalTrials.gov requests → normalized `TrialRecord`s.
3. **Decide & aggregate** – `QueryPlan.analysis` picks the aggregation; the visualization
   type is derived deterministically from the analysis kind.
4. **Specify** – aggregation result → `VisualizationSpec` (type, title, encoding, data) + meta.

The single coherent approach: every supported question compiles to the same `QueryPlan`
intermediate representation, and one executor handles every plan. New question classes are
added by adding an analysis kind, not a new code path per question.

## 2. Data source (Section 2)

- One async HTTP client (`httpx`) with retries, timeouts, pagination, a hard cap on trials
  fetched per cohort, and an in-process TTL cache keyed by request params.
- A fixed `fields=` projection so responses are small and the parser is explicit.
- A normalizer converts raw study JSON into `TrialRecord` (phase labels, start year,
  de-duplicated countries, sponsor class, intervention names/types, enrollment, MeSH terms)
  while keeping the raw field path + raw value for citations.

## 3. Inputs and outputs (Section 3)

### Request (`POST /v1/visualize`)
- `query` (string, required, 3..1000 chars)
- Optional, candidate-defined: `drug_name`, `condition`, `sponsor`, `country`,
  `trial_phase`, `status`, `start_year`, `end_year`, `max_trials`, `planner`
  (`auto|llm|rules`), `top_n`, `time_granularity`.
- Validation with Pydantic v2: enum checks, year range sanity, `start_year <= end_year`.
- Structured fields always override anything the planner infers from free text.

### Response
```
{ "visualization": { type, title, subtitle?, encoding, data[], config{} },
  "meta": { intent, plan, filters_applied, total_matched, trials_analyzed,
            truncated, source, api_requests[], notes[], warnings[], timing_ms,
            planner_used } }
```
`encoding` is per visualization type and documented with JSON-schema-style tables in the
README so a frontend can render without guessing. Every datum carries `citations[]`.

## 4. Visualization coverage (Section 4)

| Analysis kind | Viz type | Example question |
|---|---|---|
| time_trend | `time_series` | trials per year for a drug since 2015 |
| distribution | `bar_chart` | trials by phase / intervention type / status / sponsor class |
| comparison | `grouped_bar_chart` | phases for Drug A vs Drug B; sponsor class across two conditions |
| geographic | `choropleth_map` (bar-renderable fallback included) | countries with most recruiting trials |
| relationship | `network_graph` | sponsor↔drug network; drug↔drug combination network |
| scatter | `scatter_plot` | enrollment vs start year per trial |
| histogram | `histogram` | distribution of enrollment sizes |

The type is a function of the analysis kind, so a frontend can switch on `type` and rely
on the documented encoding for that type.

## 5. Deep citations (Section 5, bonus)

Every datum (bar, time bucket, node, edge, point, bin) includes
`citations: [{nct_id, field, excerpt, url}]` where `excerpt` is the exact value from the API
response that put that trial into that datum (e.g. `"PHASE3"` from
`protocolSection.designModule.phases`). Citations are capped per datum (default 25) with
`citation_count` and `citations_truncated` so payloads stay bounded; the cap is a request
parameter.

## 6. AI / agent design (Evaluation criterion 2)

- **The LLM never touches data.** Claude only produces a `QueryPlan` via structured outputs
  (`client.messages.parse` with the Pydantic model). All retrieval, counting, grouping, and
  citation building are deterministic Python.
- **Validation and constraints**: the plan schema uses enums for analysis kind, dimension,
  measure, granularity, and viz type; semantic post-validation rejects impossible plans
  (comparison with <2 cohorts, time trend without a time field, etc.).
- **Fallback**: a rule-based planner (keyword + regex over a curated vocabulary) is used when
  no API key is present, when `planner=rules`, or when the LLM plan fails validation. The
  response says which planner produced the plan.
- **Traceability**: `meta.plan` echoes the exact plan executed, `meta.api_requests` lists the
  ClinicalTrials.gov URLs called, and `meta.notes` explains interpretation choices.

## 7. Code quality, testing, submission (Sections 6–8)

- Python 3.11+, FastAPI, Pydantic v2, httpx, anthropic SDK; `pytest` suite with a recorded
  real-API fixture so aggregation tests run offline; live smoke test opt-in.
- `examples/run_examples.py` produces the 3–5 example JSON outputs from the real API.
- README: run instructions, request/response schema, design decisions, limitations,
  integrity note (tools used, validation, deliberate vs generated).
- Optional demo: a single static HTML page served at `/` that renders every spec type.
- Push to `main` of github.com/aravinds-kannappan/Clinical-Trial-Take-Home and build the
  submission zip.

## Execution order

1. Scaffold project, dependencies, config.
2. Schemas: request, plan, response (the contracts first).
3. ClinicalTrials.gov client + normalizer; record a fixture.
4. Aggregations + citations + spec builder.
5. Rule planner, then Claude planner, then router/fallback.
6. FastAPI app + error handling.
7. Tests.
8. Example runs against the live API.
9. Demo page.
10. README, zip, git push.
