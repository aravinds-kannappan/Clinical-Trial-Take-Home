# ClinicalTrials.gov Query-to-Visualization Agent

A backend service that turns a natural-language question about clinical trials into a
**structured, cited visualization specification** backed by live data from the
[ClinicalTrials.gov Data API v2](https://clinicaltrials.gov/data-api/api).

```
POST /v1/visualize
{ "query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab" }
→ { "visualization": { "type": "time_series", "title": ..., "encoding": ..., "data": [ ...rows with citations... ] },
    "meta": { "plan": ..., "cohorts": [...], "notes": [...], "warnings": [...] } }
```

- **Live demo (deployed endpoint + UI):** https://clinical-trial-viz.vercel.app (UI at `/`, API at `/v1/visualize`, docs at `/docs`). The deployment runs the rule-based planner; set `OPENAI_API_KEY` in the Vercel project to enable the LLM planner there.
- **Demo video:** [`demo/demo.mp4`](demo/demo.mp4) (also `demo/demo.webm`)
- **Example runs (actual JSON outputs):** [`examples/`](examples/) — 8 queries covering every visualization type
- **Design plan written before implementation:** [`PLAN.md`](PLAN.md)

Seven visualization types are supported from one pipeline: `bar_chart`, `grouped_bar_chart`,
`time_series`, `choropleth_map`, `network_graph`, `scatter_plot`, `histogram`. Every datum
(bar, time bucket, country, node, edge, point, bin) carries deep citations: the NCT ids and the
exact API field/value that put each trial into that datum.

---

## 1. How to run

### Install

Python 3.11+ is required (developed on 3.12).

```bash
python -m venv .venv && source .venv/bin/activate      # or: uv venv && source .venv/bin/activate
pip install -e ".[dev]"                                 # or: uv pip install -e ".[dev]"
```

### Configure (optional)

Copy `.env.example` to `.env`. Without an `OPENAI_API_KEY` the service still works end to end
using the deterministic rule-based planner; with a key, the LLM planner is used by default
(`planner=auto`) and the rule planner remains the fallback.

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | unset | Enables the LLM planner (OpenAI structured outputs) |
| `LLM_MODEL` | `gpt-4.1` | Any model supporting structured outputs (`gpt-4.1-mini`, `gpt-5-mini`, … also work) |
| `OPENAI_BASE_URL` | unset | OpenAI-compatible endpoint override |
| `DEFAULT_MAX_TRIALS` | `2000` | Trials fetched per cohort unless the request overrides it (hard cap 5000) |
| `DEFAULT_CITATIONS_PER_DATUM` | `25` | Citations kept per datum unless the request overrides it |
| `CTGOV_PAGE_SIZE` | `1000` | Page size for the ClinicalTrials.gov API (its maximum) |

### Start

```bash
uvicorn app.main:app --reload --port 8000
```

- UI demo: http://127.0.0.1:8000/
- OpenAPI docs: http://127.0.0.1:8000/docs
- Health: `GET /health` reports whether the LLM planner is enabled.

### Try it

```bash
curl -s http://127.0.0.1:8000/v1/visualize \
  -H 'Content-Type: application/json' \
  -d '{"query": "Which countries have the most recruiting trials for lupus?", "max_citations_per_datum": 2}' | jq .
```

### Test

```bash
pytest -q          # 53 tests, offline (uses a recorded API fixture); ~1s
```

### Regenerate the example runs

```bash
uvicorn app.main:app --port 8000 &
python examples/run_examples.py --planner auto     # writes examples/NN_*.json
```

### Deploy

The repo includes `vercel.json`, `api/index.py` and `requirements.txt`; `vercel deploy --prod`
publishes the API and the demo UI as one serverless function. Set `OPENAI_API_KEY` in the
project's environment to enable the LLM planner there; without it the deployment runs the rule
planner. Any ASGI host (Fly, Railway, a container running `uvicorn app.main:app`) works too.

---

## 2. Architecture

```
                 ┌──────────────┐    QueryPlan     ┌──────────────┐   TrialRecord[]   ┌─────────────┐   rows   ┌──────────────┐
 query + fields →│   Planner    │────────────────▶ │  Retriever   │─────────────────▶ │ Aggregator  │────────▶ │ Spec builder │→ response
                 │ llm | rules  │  (validated IR)  │ ctgov client │  normalize.py     │ + citations │          │ + meta       │
                 └──────────────┘                  └──────────────┘                   └─────────────┘          └──────────────┘
```

Every question compiles to the same intermediate representation, a **`QueryPlan`**
([`app/schemas/plan.py`](app/schemas/plan.py)): *what to fetch* (one or more cohorts of
filters), *how to aggregate* (analysis kind + dimension/measure/time/network/scatter/histogram
settings), and *how to present* (sort, top-N). One executor
([`app/pipeline.py`](app/pipeline.py)) runs every plan. The visualization type is a pure
function of the analysis kind, so adding a question class means adding an analysis kind and
an aggregation, not a new code path per question.

| Module | Responsibility |
|---|---|
| `app/schemas/` | Request, plan, and response contracts (Pydantic v2, closed enums) |
| `app/planner/rules.py` | Deterministic planner: keyword/regex vocabulary → `QueryPlan` |
| `app/planner/llm.py` | LLM planner: OpenAI structured outputs constrained to the `QueryPlan` schema |
| `app/planner/router.py` | Planner selection, structured-field overrides, validation, fallback |
| `app/ctgov/query_builder.py` | `CohortFilters` → API v2 parameters (`query.intr`, `filter.advanced`, …) |
| `app/ctgov/client.py` | Async HTTP client: pagination, retries, bounded fetch, TTL cache |
| `app/ctgov/normalize.py` | Raw study JSON → `TrialRecord` (phases, partial dates, countries, …) with source paths |
| `app/analysis/extract.py` | Grouping values / numeric values / entities **with evidence** (field path + exact value) |
| `app/analysis/aggregate.py` | group-by, time series, geography, scatter, histogram — all cited |
| `app/analysis/network.py` | Bipartite and co-occurrence networks with cited nodes and edges |
| `app/viz/spec_builder.py` | Deterministic titles, per-type `encoding`, rendering `config` |
| `app/main.py` | FastAPI surface (`/v1/visualize`, `/v1/plan`, `/health`, `/` demo) |
| `demo/index.html` | Single-file frontend (Vega-Lite + D3) that renders every type and shows citations on click |

---

## 3. Request schema — `POST /v1/visualize`

Content-type `application/json`. Unknown fields are rejected (HTTP 422).

| Field | Type | Required | Validation | Meaning |
|---|---|---|---|---|
| `query` | string | **yes** | 3–1000 chars | Natural-language question |
| `drug_name` | string | no | ≤200 chars | Intervention/drug; applied as `query.intr` to every cohort |
| `condition` | string | no | ≤200 chars | Condition/disease; `query.cond` |
| `sponsor` | string | no | ≤200 chars | Sponsor or collaborator; `query.spons` |
| `country` | string | no | ≤100 chars | Country/location text; `query.locn` |
| `trial_phase` | enum | no | `Early Phase 1`, `Phase 1`, `Phase 1/Phase 2`, `Phase 2`, `Phase 2/Phase 3`, `Phase 3`, `Phase 4`, `Not Applicable` | Restrict to one phase label |
| `status` | enum | no | `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `SUSPENDED`, `TERMINATED`, `WITHDRAWN`, `UNKNOWN` | Restrict to one overall status |
| `start_year` | int | no | 1900–2100, ≤ `end_year` | Earliest trial start year |
| `end_year` | int | no | 1900–2100 | Latest trial start year |
| `max_trials` | int | no | 50–5000 (default 2000) | Cap on trials fetched **per cohort** |
| `top_n` | int | no | 1–100 | Keep only the top N categories/countries |
| `time_granularity` | enum | no | `year`, `quarter`, `month` | Bucket size for time trends |
| `max_citations_per_datum` | int | no | 0–200 (default 25) | Citations kept per datum; 0 disables citations |
| `planner` | enum | no | `auto` (default), `llm`, `rules` | `llm` fails with HTTP 400 if no key is configured |

**Structured fields always override what the planner infers from `query`** and are applied to
every cohort (e.g. a comparison of two conditions can be restricted to one drug).

```json
{ "query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab" }
{ "query": "Compare phases for trials involving pembrolizumab vs nivolumab", "status": "RECRUITING" }
{ "query": "Which countries have the most trials?", "condition": "lupus", "start_year": 2020, "top_n": 15 }
```

### `POST /v1/plan` (dry run)

Same body; returns only the `QueryPlan` the service *would* execute plus `planner_used`,
`notes`, and `warnings`. Useful for debugging a planner without hitting ClinicalTrials.gov.

### Errors

All errors are `{"error": {"code", "message", "details"}}`.

| HTTP | `code` | When |
|---|---|---|
| 422 | (FastAPI validation) | Request fails schema validation |
| 422 | `plan_invalid` | The plan is semantically impossible (e.g. comparison with one cohort) |
| 400 | `planner_unavailable` | `planner=llm` but no `OPENAI_API_KEY` |
| 502 | `upstream_error` | ClinicalTrials.gov rejected the query or is unreachable after retries |

---

## 4. Response schema

```jsonc
{
  "visualization": {
    "type": "bar_chart | grouped_bar_chart | time_series | choropleth_map | network_graph | scatter_plot | histogram",
    "title": "Trials by phase for lupus",
    "subtitle": "One-sentence interpretation of the question (from the planner)",
    "encoding": { /* per-type channel map, see below */ },
    "data": [ /* typed rows, see below */ ],      // network_graph: {"nodes": [...], "edges": [...]}
    "config": { /* rendering hints: sort, unit, scale, time_granularity, ... */ }
  },
  "meta": {
    "intent": "distribution",                       // analysis kind
    "planner_used": "llm | rules",
    "llm_model": "gpt-4.1 | null",
    "plan": { /* the exact QueryPlan executed */ },
    "cohorts": [ { "label", "filters_applied", "total_matched", "trials_analyzed", "truncated", "api_requests": [urls] } ],
    "trials_analyzed": 1668,
    "notes": [ "interpretation choices and assumptions" ],
    "warnings": [ "data-quality or fallback warnings" ],
    "source": "clinicaltrials.gov",
    "source_api": "https://clinicaltrials.gov/api/v2/studies",
    "generated_at": "2026-10-07T05:12:33Z",
    "timing_ms": { "planning": 2100, "fetch": 650, "aggregate": 20, "total": 2800 }
  }
}
```

### Citations (on every datum)

```jsonc
{
  "citations": [
    { "nct_id": "NCT06866080",
      "title": "A Study of LCAR-AIO in Subjects With Relapsed/Refractory Autoimmune Diseases",
      "url": "https://clinicaltrials.gov/study/NCT06866080",
      "evidence": [ { "field": "protocolSection.designModule.phases", "excerpt": "[\"EARLY_PHASE1\"]" } ] }
  ],
  "citation_count": 18,          // trials behind this datum (always complete, even when citations are capped)
  "citations_truncated": true    // true when citation_count > len(citations)
}
```

`evidence[].field` is the JSON path in the API's study record (array indices included) and
`evidence[].excerpt` is the exact value found there. Enrollment-based measures add a second
evidence item from `designModule.enrollmentInfo.count`; network edges carry one evidence item
per endpoint entity; scatter points carry one per axis.

### Rows and encodings per type

Channel objects have the shape `{"field", "type": nominal|ordinal|quantitative|temporal, "title", "unit"?}`
plus type-specific extras. `tooltip` lists row fields worth showing on hover.

| `type` | Row fields (each row also has the citation fields) | `encoding` keys | Notable `config` |
|---|---|---|---|
| `bar_chart` | `category`, `value`, `series: null` | `x` (category), `y` (value), `tooltip` | `sort` (`natural\|desc\|asc`), `dimension`, `top_n`, `unit` |
| `grouped_bar_chart` | `category`, `series`, `value` | `x`, `y`, `series`, `tooltip` | `series_order`, `grouping: side_by_side`, `dimension` |
| `time_series` | `period` (`2019`, `2019-Q2`, `2019-06`), `period_start` (ISO date), `value`, `series` | `x` (temporal, `label_field: period`), `y`, `tooltip` | `time_granularity`, `time_field`, `gaps_filled_with_zero: true` |
| `choropleth_map` | `country`, `iso3`, `iso_numeric`, `value` | `location` (`lookup: iso_3166_1_alpha3`, `numeric_id_field`), `color`, `label`, `tooltip` | `fallback: bar_chart` + `fallback_encoding` for renderers without maps; `color_scale: sequential` |
| `network_graph` | `data.nodes[]`: `id`, `label`, `type`, `weight`; `data.edges[]`: `source`, `target`, `weight` | `nodes` {id,label,group,size}, `edges` {source,target,weight} | `node_types`, `bipartite`, `min_edge_weight`, `max_nodes`, `layout: force_directed` |
| `scatter_plot` | `nct_id`, `label`, `x`, `y`, `color` | `x`, `y`, `label`, `color`?, `tooltip` | `x_field`, `y_field`, `y_scale` (`log` for enrollment), `point_is_trial: true` |
| `histogram` | `bin_start`, `bin_end`, `bin_label`, `count` | `x` (`bin_end_field`, `label_field`), `y`, `tooltip` | `scale` (`linear` or `log10`), `field`, `requested_bins` |

A renderer therefore needs one `switch(type)` and can read fields by name. The bundled
`demo/index.html` is a reference implementation of exactly that contract.

### The `QueryPlan` (echoed in `meta.plan`)

```jsonc
{
  "analysis": "time_trend | distribution | comparison | geographic | relationship | scatter | histogram",
  "cohorts": [ { "label": "Pembrolizumab",
                 "filters": { "intervention"?, "condition"?, "sponsor"?, "country"?, "free_text"?,
                              "statuses"?: [...], "phases"?: [...], "study_type"?, "sponsor_class"?,
                              "start_year_from"?, "start_year_to"? } } ],
  "dimension": "phase | status | sponsor_class | sponsor | collaborator | intervention_type | intervention | condition | country | study_type | primary_purpose | allocation | sex | start_year",
  "measure": "trial_count | enrollment_total | enrollment_mean | enrollment_median",
  "time_field": "start_date | primary_completion_date | completion_date | first_posted_date",
  "granularity": "year | quarter | month",
  "network": { "node_types": ["sponsor","intervention"], "min_edge_weight": 1, "max_nodes": 40 },
  "scatter": { "x": "start_year", "y": "enrollment", "color": "phase" },
  "histogram": { "field": "enrollment", "bins": 10 },
  "top_n": 15, "sort": "natural | desc | asc", "interpretation": "..."
}
```

---

## 5. Query coverage

| Question class | Example | Analysis → type |
|---|---|---|
| Time trends | "How has the number of trials for pembrolizumab changed per year since 2015?" | `time_trend` → `time_series` |
| Distributions | "How are lupus trials distributed across phases?", "most common intervention types for melanoma" | `distribution` → `bar_chart` |
| Comparisons | "Compare phases for pembrolizumab vs nivolumab", "compare sponsor categories across psoriasis and asthma" | `comparison` → `grouped_bar_chart` |
| Geography | "Which countries have the most recruiting trials for lupus?" | `geographic` → `choropleth_map` |
| Relationships | "network of sponsors ↔ drugs for breast cancer", "which drugs co-occur in combination studies" | `relationship` → `network_graph` |
| Per-trial relationships | "Enrollment vs start year for phase 3 pembrolizumab trials" | `scatter` → `scatter_plot` |
| Numeric distributions | "How large are Alzheimer's trials?" | `histogram` → `histogram` |

Fourteen grouping dimensions, four measures (trial count and total/mean/median enrollment),
four time fields, three granularities, six network entity types (sponsor, collaborator,
intervention, condition, country, investigator) and seven numeric fields are all reachable
through the same plan, so the combinations above are a sample rather than a list of
hard-coded cases. See [`examples/`](examples/) for actual outputs.

---

## 6. Key design decisions and trade-offs

**One intermediate representation.** The brief asks for a single coherent approach across
many question types. The `QueryPlan` is that approach: planners only decide *what* to do, in a
closed vocabulary; the executor decides *how*. The trade-off is that questions outside the
vocabulary (e.g. "which trials had the most adverse events?") are not expressible — by
design, the system says what it cannot do rather than improvising.

**The LLM never touches data.** The model's only job is to emit a `QueryPlan` through
structured outputs constrained to the Pydantic schema. Every number in a chart is computed in
Python from API responses; entity names the model extracts are used only as search terms, and
`meta.cohorts` reports what actually matched. This removes the most hallucination-prone step
(an LLM inventing counts or trial ids) entirely. Plans are re-validated after parsing
(`validate_plan`), and a failed or unavailable LLM falls back to the rule planner with a
visible warning instead of failing the request.

**A rule-based planner is a first-class citizen, not a stub.** It makes the service usable
with no key, keeps tests deterministic, and acts as the safety net. It is conservative: when it
cannot tell whether "X" is a drug or a disease it uses the API's full-text search and says so
in `meta.notes`. The LLM planner is better at entity typing and phrasing; both produce the
same plan schema so everything downstream is shared.

**Structured fields are authoritative.** Optional request fields override the planner on every
cohort, and the LLM is shown them as context so its labels and interpretation match. This gives
callers a deterministic escape hatch when free text is ambiguous.

**Deep citations by construction.** Instead of a second pass that tries to find supporting
text, every extracted value carries the JSON path and exact value it came from
(`Extracted.evidence`), and aggregation buckets keep the records that fed them. Citations are
capped per datum (default 25, configurable, `citation_count` always complete) because an
uncapped country bar can reference thousands of trials.

**Bounded, explicit data access.** Each cohort fetch is capped (`max_trials`, default 2000,
hard max 5000) with a fixed field projection, pagination, retries with back-off, and a TTL
cache. When a cohort is truncated the response says so (`truncated`, `warnings`), so a
"top countries" chart over the first 2000 of 5000 trials is never silently presented as
complete. The alternative — exhaustively paging every match — would make broad queries
("all cancer trials") take minutes.

**Real-world data handling.** Phases are lists (`PHASE1`+`PHASE2` → "Phase 1/Phase 2"),
dates are partial (`2027-12`), countries repeat per site (de-duplicated per trial), intervention
names are free text (case/whitespace normalised; placebo/sham/standard-of-care arms excluded from
drug groupings and networks), conditions are grouped by MeSH term when present, enrollment is
heavy-tailed (log10 histogram bins), and multi-valued dimensions are flagged in `meta.notes`
because bar totals can exceed trial counts.

**Visualization type is derived, not chosen.** Each analysis kind maps to one type with a fixed
encoding. This is less flexible than letting a model pick chart types, but it makes the output
contract unambiguous for a renderer and keeps the LLM out of presentation decisions it has no
data to make. Geographic answers ship as `choropleth_map` with a documented `bar_chart`
fallback encoding for renderers without maps.

**Networks that mean something.** One node type gives a co-occurrence graph (drug ↔ drug within
the same trial); two give a bipartite graph (sponsor ↔ drug). Node weight is the number of
trials mentioning the entity, edge weight the number of trials containing both; pruning keeps
a balanced number of nodes per side so one sponsor cannot crowd out every drug. Every node and
edge is cited.

---

## 7. Limitations and what I would improve with more time

- **Relevance ordering under truncation.** When a cohort exceeds `max_trials`, the first pages
  in the API's default order are analysed. Exposing the API's `sort` parameter (e.g. newest first)
  and streaming larger cohorts in the background would make truncated results more predictable.
- **Entity normalisation is lexical.** "Pembrolizumab", "MK-3475" and "Keytruda" are different
  nodes. Mapping intervention names to the API's MeSH `interventionBrowseModule` terms (already
  parsed) or to RxNorm would merge synonyms; the same applies to sponsor name variants.
- **Rule planner coverage.** It handles the question classes in the appendix and common
  variations, but it is a vocabulary, not a parser; unusual phrasings fall through to free-text
  search. An evaluation set of questions with expected plans (the tests are the seed of one) would
  let both planners be measured and tuned.
- **No multi-step agent loop.** The planner is a single structured-output call. For questions that
  need clarification ("trials for X" where X is unknown) a clarify-or-proceed step and a tool for
  checking whether an entity exists in the API (`/stats/field/values`) would reduce empty results.
- **Statistics are descriptive.** No significance testing, no normalisation by denominator (e.g.
  trials per capita for countries). Those would be additional measures in the plan vocabulary.
- **Operational hardening.** The TTL cache is in-process; a shared cache (Redis) and request
  rate limiting would be needed for multi-instance deployment. The serverless deployment has a
  60-second budget, so very large `max_trials` values should be run against a long-lived host.
- **Frontend.** The demo is a single file with CDN libraries; it is a reference renderer, not a
  product. Choropleth lookups rely on ISO numeric codes from `pycountry`; a handful of
  ClinicalTrials.gov country names are unmapped and are listed in `warnings`.

---

## 8. Integrity note: tools, validation, and what was deliberate

**Tools used.** The code was written with Claude Code (Anthropic's agentic coding tool) under my
direction, with the OpenAI API (`gpt-4.1`) as the runtime LLM planner. The ClinicalTrials.gov
API was probed directly with `curl` before any code was written to confirm which parameters,
filters and field names actually exist (see `PLAN.md`, section 0).

**How correctness was validated.**
- 53 offline tests (`pytest`) cover normalisation of messy records, the query builder's API
  syntax, plan validation rules, the rule planner on every appendix question class, every
  aggregation (including citation caps, gap filling, log bins, bipartite edges), and the HTTP
  contract for all seven visualization types with a recorded real-API fixture
  (`tests/fixtures/pembrolizumab_200.json`, 200 live studies).
- The LLM path is tested with mocks (plan used when valid; fallback with a warning when it
  fails; structured fields passed as context) and was exercised live against the OpenAI API for
  the example queries.
- Every example in `examples/` is the unmodified HTTP response from the running service; totals
  were spot-checked against the ClinicalTrials.gov website (e.g. the count of pembrolizumab
  studies) and the choropleth was checked for correct ISO joins in the demo UI.
- The demo UI was exercised in a browser for each visualization type, including citation
  click-through, and the walkthrough was recorded as the demo video.

**Deliberate design vs. generated-and-adapted.** The architecture (single `QueryPlan` IR,
LLM-only-plans/never-touches-data, rule planner as fallback and oracle, evidence-carrying
extraction for citations, bounded fetches with explicit truncation, derived visualization
types with fixed encodings) and the request/response contracts were designed deliberately and
are documented in `PLAN.md` before implementation. The bulk of the code was generated from
those designs and then iterated: the rule planner's entity extraction went through several
rounds against real questions, the Vega/D3 renderer was debugged in the browser, and the
pipeline was adjusted after seeing live outputs (consistent category ordering across comparison
cohorts; passing structured fields to the LLM so labels match). The test suite was written
alongside the code and drove several of those fixes.

---

## Appendix: project layout

```
app/
  main.py            FastAPI app and error handling
  pipeline.py        plan → fetch → aggregate → spec
  config.py          settings from env / .env
  schemas/           common enums, request, plan, response
  planner/           rules.py, llm.py, router.py, base.py
  ctgov/             query_builder.py, client.py, normalize.py
  analysis/          extract.py, aggregate.py, network.py, geo.py
  viz/spec_builder.py
demo/index.html      reference renderer (Vega-Lite + D3) and demo video
examples/            run_examples.py and 8 recorded outputs
tests/               53 tests + recorded API fixture
api/index.py, vercel.json, requirements.txt   serverless deployment
PLAN.md              the plan written before building
```
