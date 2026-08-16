# The location-analysis agent graph

The map's location-analysis agent, rebuilt server-side where it can sit directly on the
warehouse. It eventually replaces the hand-rolled loop in
`src/lib/server/services/ai-prompt.ts`; until the Next.js side switches endpoints, that file
remains the live implementation and the **authority on the product surface** — the stream
event union, the report field names, and the enum vocabularies all come from there and from
`src/lib/regional-intelligence.ts`.

## Topology

```
gather_warehouse_evidence ──▶ assess_sufficiency ──┬─(insufficient)─▶ web_evidence ─▶ synthesize_report
                                                   └─(sufficient)──────────────────▶ synthesize_report
```

Four nodes, declared as frozen dataclasses with typed outputs, walked by `execute_graph()`.
The edges are in `GRAPH_EDGES` as data so the topology can be asserted rather than inferred,
and `test_agent_graph.py` does assert it.

The shape is the point. Two of the four nodes are model-driven; the edge between them is
not. `assess_sufficiency` is ordinary Python operating on a ledger of what the warehouse
tools actually returned, so **whether a request is allowed to touch the public web is
decided by the service, not by the model**. The TypeScript version left that to the model,
bounded only by a `MAX_SEARCHES_PER_REQUEST` counter enforced when the tool was called; here
the web-search tool is not even present in the request until the gate opens it.

The budget rule mirrors `ai-prompt.ts`'s intent, expressed as coverage rather than rounds:

| Distinct warehouse tools that returned rows | Verdict | Search budget |
|---|---|---|
| 0 | insufficient | 3 (`MAX_SEARCHES_PER_REQUEST`) |
| 1 | insufficient | 2 |
| 2+ **and** the caller asked a specific question **and** coverage is partial | insufficient | 1 |
| 2+ otherwise | sufficient | 0 |

`gather_warehouse_evidence` seeds the transcript with the replayed history (last
`MAX_HISTORY_TURNS`, mirroring the TypeScript cap) and the volatile location context. Only
`synthesize_report` produces user-visible structure; every node before it produces evidence.

## Why the tool runner inside Sanic, and not Managed Agents

Managed Agents would run the loop and host the sandbox for us, and is the better default for
most agents. It is the wrong fit here for one decisive reason: **the warehouse DSNs are
private**. The tools are SQL against `published_reader`, reachable only from inside our own
network, and there is no way to hand an Anthropic-hosted sandbox that connectivity without
either exposing the database publicly or building a host-side custom-tool bridge — at which
point we are hosting the compute anyway and have paid for the platform without using it.

So: `client.beta.messages.tool_runner` (SDK beta helper) with `@beta_async_tool` warehouse
tools, hosted in the Sanic process that already holds the reader pool. Not a hand-rolled
`while stop_reason == "tool_use"` loop, and not the Claude Agent SDK, which is a different
package (Claude Code as a library) with built-in filesystem tools we have no use for.

### What the SDK does not do for us

The Python tool runner **does not auto-resume `pause_turn`**, and unlike the TypeScript
runner it cannot be resumed in place — it exits unconditionally when no client tool ran. A
paused turn therefore looks like a completed one: no error, no warning, just a truncated
answer. `_run_pass` handles it explicitly by mirroring the transcript onto `ctx.messages` as
it iterates and starting a fresh runner from there (the transcript already ends with the
paused assistant turn), bounded by `MAX_PAUSE_RESTARTS`. This only ever fires on the web
pass, since server-side tools are what pause a turn — but the guard lives in the shared
helper so it cannot be forgotten if another server tool is added.

Mirroring the transcript is also what lets a *later node* resume from an earlier one's work:
the runner keeps its own copy of the conversation and does not expose it.

## Tool contract

Every tool in `tools.py` is **read-only and bounded**, and both properties are enforced in
Python and SQL rather than requested in the prompt.

- **Read-only.** Every statement is a `SELECT` issued on a `published_reader` session. A
  writer session is never used. `test_every_tool_statement_is_read_only` asserts no mutating
  verb appears in any executable line of any agent statement.
- **Least privilege.** The session comes from `published_reader_session()`, which already
  falls back to the combined-local session in the local profile the way the other routes do.
  A different provider can be injected for one run through `tools.run_context(...)`; that is
  how the unit suite stubs the database.
- **Bounded, with the bound reported back.** Radius (50 km), lookback (10 years of signals,
  10 years of drought weeks, 45 years of fire), row caps, and pre-aggregation fan-out caps
  are constants in `tools.py`. An over-large argument is **clamped, not rejected**, and the
  clamped value is echoed in the result's `applied_bounds` so the model can see what it
  actually received rather than silently reasoning over a window it did not get.
- **Summaries, not rows.** `signals_near_point` and `fire_history_near_point` aggregate in
  the database. Handing a model several thousand point detections is both expensive and
  worse — the caps exist so the worst case is predictable, and the aggregation exists so the
  typical case is useful.
- **Absence is stated, never implied.** Every payload carries a `note` distinguishing "the
  warehouse holds no record here" from "the condition is absent". The system prompt repeats
  the rule; the payload makes it unavoidable.

Ambient state (the session provider, the per-run tool ledger, the per-run plane-probe cache)
travels in `ContextVar`s because a tool function's signature *is* its model-facing schema — a
`session` parameter would become something the model is asked to supply.

## Reading the pre-aggregated planes

Every tool that used to read a raw observation table now reads the matview the **map** reads.
That is not primarily a cost decision, though it is that too: it is the only structural
guarantee that **the agent cannot contradict the screen**. If the agent answered from
`agri.signal_observation` while the map painted from `geo.mv_signal_cell_daily`, the two could
disagree about the same cell on the same day — different quality filters, different day
derivation, different refresh moment — and the agent would state something the user can see is
false.

| tool | reads |
|---|---|
| `signals_near_point`, `signal_value_on_day`, `signal_neighbors_in_time`, `nearest_signal_cells` | `geo.mv_signal_cell_daily` ⋈ `agri.spatial_cell` |
| `signal_coverage_on_day` | `agri.signal_coverage_audit` (unchanged — see below) |
| `drought_history_at_point` | `geo.mv_drought_release_index` ⋈ `geo.drought_areas` |
| `fire_history_near_point` | `geo.features` + `geo.mv_feature_observation_day` |
| `forecast_summary_for_cell` | `agri.mv_forecast_ml_daily_serving` |
| `observation_coverage_on_day`, `observation_temporal_neighbors` | `geo.v_observation_day_census` |
| `feature_value_near_point` | `geo.features`, via `ix_features_layer_observation_day` |

Four consequences, each of which shows up in a payload and therefore in a note:

- **Quality, lane, unit and scope are all inherited, not filtered.** `geo.mv_signal_cell_daily`
  keeps only rows that are observed and quality-accepted, and it joins the **19 governed
  `(signal_name, normalized_unit, lane)` triples** — not the 19 signal names alone
  (`execution/coverage_contract.py`, verified against `agri.data_source` 2026-08-11). Both halves
  of that triple matter and neither is visible from this service:
  - the **unit** is pinned because `geo.soil_field_observation` (drizzle/0016, 0019) and
    `geo.climate_field_observation` (drizzle/0020) join signal name to an exact
    `normalized_unit`, and every app reader pins it too. Without it, one off-contract unit for a
    governed name gives the agent two rows per (cell, day) — a second, differently-scaled value
    for a signal the map serves in exactly one unit — because these tools `DISTINCT ON` /
    `GROUP BY (signal_name, support_key, normalized_unit)` rather than pinning the unit;
  - the **lane** is pinned because `geo.climate_field_observation` gates
    `source.key = 'nasa-power-daily'`, and drizzle/0020's own header records that `support_key`
    cannot substitute for it ("`surface` is a generic support the ERA5-Land writer also emits").
    `precipitation`, `wind_speed` and `relative_humidity` are shared names; without the lane gate
    a non-NASA row is in the rollup the agent reads and absent from the view the map draws.

  No `is_observed`, `quality_flag`, unit, lane or signal-scope predicate remains in any tool
  statement, because there is no column left to write one against. **If the rollup's defining
  query ever stops applying those filters, every signal tool silently starts reporting imputed,
  off-lane or off-unit values**, and no test in this service can see it. That coupling is the
  price of the repoint and it is stated here rather than buried.
- **`source_parameter` is gone.** The rollup's grain is
  `(support_key, signal_name, normalized_unit, cell_id, observed_day)` and carries no upstream
  parameter column. Under the governed contract a signal name resolves to one parameter within
  one support key anyway; emitting a parameter the rollup cannot distinguish would be inventing
  one. Answers are grained `(signal, support, unit)`.
- **Days are dates, not midnight pairs.** The rollup's grain *is* the calendar day, derived once
  where it is built. The old half-open `day_start`/`day_end` bracket existed to keep an index on
  a raw `observed_at` usable; there is no `observed_at` left to bracket. `signal_coverage_on_day`
  is the one exception and still binds both, because `agri.signal_coverage_audit` is grained by
  the *window a lane fetched* rather than by a day.
- **`forecast_summary_for_cell` narrowed.** `agri.mv_forecast_ml_daily_serving` covers ML-method
  forecasts on series flagged `allow_ml_daily_aggregate`, aggregated to one row per valid **day**
  — so `horizon_step` is gone and a published non-ML forecast is out of scope rather than absent.
  There is deliberately **no fallback** to `agri.v_forecast_series_serving`: falling back would
  reintroduce the eight-table join precisely when the box can least afford it.

### Why `signal_coverage_on_day` still reads a raw table

The rule is not "no aggregates"; it is that no request may read far more rows than it returns.
That read is bounded on both sides already — capped at `MAX_CELL_FANOUT` cells before the audit
is touched, then to the audit rows overlapping one day. It is also the one question the census
**cannot** answer: `geo.mv_signal_observation_day` is grained by catalogue surface and day and
says *how much landed*; `agri.signal_coverage_audit` is grained by signal, cell and fetched
window and says *why nothing did*. Folding a reason-for-absence ledger into a how-much-landed
census would destroy the column that makes an empty day explainable.

### Refusing an unbuilt plane

A matview can exist while holding nothing: PostgreSQL creates it `WITH NO DATA` and **raises**
rather than returning zero rows until a `REFRESH` has run. `agri.mv_forecast_ml_daily_serving`
shipped in exactly that state — created, indexed, never refreshed, its refresher reading an
environment variable nobody set.

That leaves two bad options and one good one. Letting the raise escape surfaces to the model as
an unexplained tool error. Catching it and returning `[]` is **far worse**, because "no drought
here" and "the drought plane was never built" become the same answer. So every tool probes the
relations it is about to read (`sql/agent/materialized_plane_populated.sql`, a `pg_class`
lookup touching no user data) and returns a **typed refusal naming the relation**. Silence about
a relation counts as unbuilt: fail closed, not open.

The probe's answers are cached per `run_context`, because a matview cannot become unpopulated
again once refreshed. `geo.v_observation_day_census` is a plain **view** and reports itself
populated regardless of the matviews beneath it, so the probe always names
`CENSUS_RELATIONS` — the three matviews — and never the view that unions them.

## The generic surface triad

Section 11 of `docs/layer-lane-standard.md` obliges *every* layer to answer three questions, and
only the signal plane had all three. Eleven more bespoke tool sets would repeat the mistake the
pre-aggregation work exists to undo — many relations answering one question — so instead there
are three tools parameterised by `surface_name`, reading the same relations the app reads:

| tool | question | source |
|---|---|---|
| `observation_coverage_on_day` | is this day covered at all, and where does it sit in the surface's history | `geo.v_observation_day_census` |
| `observation_temporal_neighbors` | nearest covered day each side, with `distance_days` | same census, two one-row index probes |
| `feature_value_near_point` | nearest published features on that day, with `distance_meters` | `geo.features` |

`AGENT_SURFACE_NAMES` holds **24 hand-spelled names** — 11 `geo.layers` rows, 4
`SLIDER_STREAM_LAYER_NAMES`, 9 `climate-field-<signal>` streams — for the same reason
`docs/layer-lane-standard.md` §9 requires a hand-spelled catalogue assertion: a derived list
drifts with the thing it is meant to check. If this were built from a query, a layer that
vanished from the database would vanish from the agent's vocabulary too, and the agent would say
"I do not know that surface" instead of "that surface stopped being served".

Two design points that are load-bearing rather than incidental:

- **An uncovered day is answered three ways, not one.** `observation_coverage_on_day` returns
  the surface's earliest and latest served days beside the verdict, so the model can say *before
  this lane's horizon* / *past its live edge* / *a real hole in the middle* rather than merely
  "empty". Those are three different facts and only one of them is a bug.
- **Refusals name the gap.** An unknown surface, a stream handed to `feature_value_near_point`,
  or an unbuilt matview all produce a typed refusal listing what *is* answerable — never an
  empty result, which the model reads as an absence.

### The bounding-box prefilter

`ST_DWithin(geom::geography, …)` is exact and correct, and the cast makes it unusable by
`idx_features_geom` / `drought_areas_geom_gist`, which are GiST indexes over the **geometry**
column — and no geography index exists on either table. The planner's only option was a scan
that detoasted every published feature's `properties` on the way past. Every distance query now
puts `geom && ST_Expand(point, bbox_degrees)` in front of the exact test; `&&` is a strict
superset, so it changes how many rows are examined and never which rows come back.

`bbox_degrees` is computed **per latitude** (`_bbox_degrees`). A degree of latitude is a fixed
110,574 m; a degree of longitude is 111,320 m only at the equator and shrinks by `cos(latitude)`.
Sizing the box on the latitude figure alone clips its east–west edges away from the equator and
silently drops real features — which is exactly the failure a prefilter must not introduce.

## Answering at the selected day

Three tools exist purely to satisfy §11 of the layer-lane standard for the signal plane, and
they are a different shape from the four above: those summarise a *window* and are free to
answer from whichever days inside it happen to hold readings; these answer about **one day, the
day the map is showing**.

| tool | question | statements |
|---|---|---|
| `signal_value_on_day` | what was measured on this exact day | `signal_value_on_day.sql` + `signal_coverage_on_day.sql` |
| `signal_neighbors_in_time` | what is the nearest reading each side of it | `signal_neighbors_in_time.sql` |
| `nearest_signal_cells` | where are the measurements, and how far | `nearest_signal_cells.sql` |

Design rules, each of which has a test:

- **`day` is required, and it is a string.** No default. A defaulted day is exactly how a tool
  drifts back to "latest" and starts answering a different question than the one asked. It is
  `str` rather than `date` because the signature *is* the published JSON schema; it is parsed
  with `date.fromisoformat` and an unparseable value is **refused**, never replaced with today.
  Substituting a date is the same refusal MTBS makes for a fire year with no dated release.
- **The day filter is a half-open pair of UTC midnights**, computed in Python and bound as two
  timestamps. Not a per-row `::date` cast, which would defeat the index on `observed_at`.
- **Every proximity answer carries its distance and the observation's own date.** Temporal rows
  carry `observed_day`, `nearest_cell_observed_at`, signed `day_offset` and magnitude
  `distance_days`; spatial rows carry `distance_meters` and the centroid coordinates. A
  neighbour handed back without its gap is indistinguishable from an exact match, which is the
  same class of bug as a lane reporting success having written nothing.
- **`nearest_signal_cells` LEFT JOINs its day counts.** An INNER join would drop cells holding
  nothing, and "the nearest cells" would silently mean "the nearest cells that had data" — the
  substitution the tool exists to expose. A cell with nothing comes back with a count of `0`.
- **Absence is explained from the table that already records it.** `signal_value_on_day` reads
  `agri.signal_coverage_audit` over *exactly* the cells the value came from, so a `no_data`
  verdict can only explain the point it was recorded for. Nothing new is written anywhere; the
  ingest lanes fill that table and this only reads it back. See
  `execution/coverage_contract.py` for how the same rows drive gap detection.

`AgentRequest.selected_day` carries the map's day into `build_location_context`, which states it
outright. When it is `None` the context says so and stands in today's date **visibly**, with an
instruction to name the queried day in the answer — an unstated substitution would put the model
in the position of implying a past reading is current.

### Deviations

- `signal_value_on_day` issues **two** statements for one tool call. Every other tool is one
  statement, so `test_every_tool_statement_is_read_only` drives all ten published tools and
  asserts `len(WAREHOUSE_TOOLS) == 10` beside the statement count. Both halves must be edited to
  add a tool, which is the point: the tripwire scans every statement the model can reach, and a
  count alone would let an eleventh tool ship unscanned. The plane probe is excluded from that
  count and asserted separately — it fires at most a few times per run, not once per tool, and
  pinning its exact count would make the assertion depend on tool ordering.
- `observation_coverage_on_day` and `observation_temporal_neighbors` take **no coordinate**. They
  ask about a whole map surface on a day, so a longitude would be a parameter they had nothing to
  do with. Every tool is still keyed by something the service validates — a range-checked
  coordinate, or a surface name checked against the hand-spelled catalogue —
  so `test_tool_schemas_publish_bounded_arguments` branches on the tool name rather than
  requiring coordinates of all ten.

### Where the columns come from

`agri.*` columns are verified against `models/historical.py`, `models/forecasting.py` and the
declarative views under `db/agri/`. `forecast_summary_for_cell` reads
`agri.mv_forecast_ml_daily_serving`, which is built on `agri.v_forecast_series_serving` and so
inherits its "published, finalized, validated" gate — the agent must not be able to quote a draft
forecast, and reading the matview rather than re-deriving the join keeps that true.

`geo.*` columns come from a different source of truth: the Next.js Drizzle schema
(`src/lib/server/db/schema.ts`) and the `drizzle/` migrations, not this service's ORM. Tools that
read `geo.features` reference only columns that schema declares (`id`, `layer_id`, `geom`,
`properties`, `status`, `geometry_id`, `data_available_at`); `geo.drought_areas` contributes only
`valid_date`, `dm_category`, `ingested_at` and `geom` — the last as a filter, never a projection,
because that table hides about 495 MB of TOAST behind 1,040 rows. The pre-aggregated `geo.mv_*`
relations are created by the drizzle tree, which is why they can be referenced here without a
cross-migration ordering hazard.

`fire_history_near_point`'s layer names come from `ingest/firms.py` and `ingest/mtbs.py` via
their existing call-time resolvers rather than being re-spelled here, so a renamed layer moves
in one place. `feature_value_near_point` takes its layer name from the caller and checks it
against `FEATURE_SURFACE_NAMES`, so an unknown name is a refusal rather than an empty result.

`feature_value_near_point` projects `properties` through a fixed `FEATURE_PROPERTY_KEYS`
allow-list, harvested 2026-08-15 from the eight `geo.*_tiles` functions in `drizzle/` and the
`properties->>` reads in `src/lib/server/services/`. `SELECT properties` on that table is how a
bounded row count becomes an unbounded byte count — roughly 1,467 MB of TOAST across 4.97 million
rows — so a fifty-row answer would otherwise be tens of megabytes.

Non-trivial SQL lives in `sql/agent/*.sql` behind `load_query_sql`, with the beginner-doc
header standard from `sql/AGENTS.md` — including its bind-param trap: parameter names in
comments carry no leading colon, because `text()` scans comments too.

## Report vocabulary

`report.py` mirrors `ai-prompt.ts`'s `REPORT_TOOL` **field for field**, camelCase included:
`riskSummary{level, headline, factors, evidenceOrigin, evidenceSources}`, `observations[]`,
`remediation[]`, `professionalConsultation`. The enum members are the Python projection of
`src/lib/regional-intelligence.ts`, which stays the single definition; drift is a contract
break, and `test_report_rejects_a_vocabulary_the_frontend_cannot_render` is the tripwire.
The camelCase field names carry a file-level `ruff: noqa: N815` for exactly this reason —
these are wire names, not Python identifiers we are free to restyle.

`disclaimer` and `citations` are deliberately *not* fields of the report model, matching the
TypeScript split: the disclaimer is a constant (`AI_GENERATED_DISCLAIMER`, sent once as the
stream's first frame) and citations ride the `sources` event. Putting either inside the
model would invite the model to paraphrase a legally load-bearing sentence, or to invent a
citation to fill a required field.

The report is produced by **structured outputs** (`client.beta.messages.parse` with
`output_format=RemediationReport`), not by forcing a report tool on the last round the way
the TypeScript side does. Same guaranteed shape, one fewer moving part, and the failure mode
is a validation error rather than a plausible-looking tool call with a missing field.

## Caching

Render order is `tools` → `system` → `messages`, so the breakpoint on the last (only) system
block caches the tool definitions and the system prompt together. `SYSTEM_PROMPT` is
byte-stable by construction: no coordinates, no timestamps, no question. Everything
request-specific is built by `build_location_context` into the first *user* message, after
the breakpoint.

The three phases have three different tool sets — warehouse tools, warehouse tools plus
`web_search`, and none at all for the report round — so they occupy three cache prefixes
rather than one. That is unavoidable (changing `tools` invalidates everything after it), and
harmless: each prefix is stable *across requests*, which is where the reuse actually is.

## SSE contract

`POST /agent/analyze` with `{longitude, latitude, precision, question?, history?}`, validated
by pydantic at ingress, answers `text/event-stream`. Each frame is a standard SSE record
named by its own event type, with the whole event as JSON in `data:`.

| Event | Payload | Mirrors |
|---|---|---|
| `text` | `{text}` | `AgentStreamEvent` |
| `search` | `{query, resultCount}` | `AgentStreamEvent` |
| `sources` | `{sources: [{title, url}]}` | `AgentStreamEvent` |
| `report` | `{report}` | `AgentStreamEvent` |
| `refusal` | — | `AgentStreamEvent` |
| `disclaimer` | `{disclaimer}` | additive; sent first |
| `progress` | `{node, status, detail}` | additive; node lifecycle |
| `error` | `{message}` | additive; the run failed |

The first five are the TypeScript union verbatim, so a renderer switching endpoints keeps
its existing cases. The last three are additive and safely ignorable — a `switch` over the
union simply will not match them. `text` events are per-delta: the graph streams the tool
runner rather than waiting for whole messages, so narration appears while tools run.

Nodes publish onto an `asyncio.Queue`; the route drains it until a `None` sentinel. The
graph runs as a task so a dropped client cancels the run instead of leaking it, and any
exception becomes an `error` event followed by the sentinel — a failed run closes the
stream, it never hangs it.

## Deploy

The service already exposes a web CMD and the blueprint is registered in every profile, so
there is nothing to build. Enabling the agent in production is two steps, **neither of them
ours**:

1. Set `ANTHROPIC_API_KEY` on the Railway service. Until it is set, `/agent/analyze`
   answers `503 {"code": "agent_disabled"}` and every other route is unaffected — the key is
   read through `settings.anthropic_api_key`, and the SDK client is constructed per request,
   never at app start.
2. Front `/agent` behind the Next.js proxy. `/agent` is **not authenticated**, exactly like
   `/ops`; it must not be publicly reachable, and it costs money per request, which makes an
   open endpoint worse than merely leaky.

Reads go through `published_reader`, so the agent works in the `combined_local` and
`published_reader` profiles. In `receiver_writer` the reader session raises rather than
quietly borrowing the writer — a deliberate fail-closed, not an oversight.

## Model configuration

`claude-opus-5`. The `thinking` parameter is **never sent**: adaptive thinking is this
model's default, and re-specifying it buys nothing while risking a 400 if the effort setting
ever moves. Server-side fallbacks are on by default (`fallbacks="default"` with the
`server-side-fallback-2026-07-01` beta) so a safety-classifier decline is retried on the
recommended fallback model server-side instead of surfacing as a dead request; `"default"`
is used rather than a pinned model so we owe no migration when the recommendation changes.
`stop_reason == "refusal"` is still checked on every turn — the fallback chain can itself
refuse, and that is what the `refusal` event reports.

### The selected day has to reach the route, or the whole surface answers nothing

`AgentRequest.selected_day` alone is not wiring. `routes/agent_analysis.py` declares its ingress model
with `extra="forbid"`, so until `AgentAnalyzeRequest` carried a `selected_day` field a caller sending
one got a 400 and a caller omitting it left `selected_day=None` forever. `build_location_context` then
stands in `as_of.date()` and tells the model to pass it to every tool -- and today is past the live
edge of every lane (NASA POWER's newest day was 2026-08-06 and ERA5-Land's 2026-08-02, measured
2026-08-11). The result was `signals_on_day: []` and `observation_count_on_day: 0` on **every** real
request, for locations holding four years of data.

The field is now declared on the ingress model and passed straight through.
`test_the_analyze_route_accepts_the_selected_day_and_carries_it_into_the_request` pins both halves.
The remaining half is outside this service: the Next.js caller must send the slider's day.

### A row cap that nobody is told about is a fabricated absence

`signals_on_day`, `coverage_audit_on_day` and `temporal_neighbors` are capped at 40, 40 and 80 rows,
and each tool's note tells the model that a missing signal or a missing side is a statement about the
data. That is only true while the list is complete, so `*_truncated` booleans now travel in the
payload and the notes name them. Nineteen signals are under contract today so no cap binds, but the
claim was wrong by construction and is the same class as a lane reporting success having written
nothing.

### `nearest_signal_cells` counts one plane, and says so

`observation_count_on_day` is a census over `agri.signal_observation` only, while the tool returns
cells from every grid -- including `sentinel2-ndvi-0p25deg`, whose NDVI lands on
`agri.forecast_observation`. The note previously read "0 is an answer, not an omission", which is
section 3's named failure: a census over one plane reporting healthy lanes as dead. Both the note and
the model-facing docstring now name the plane, and `test_nearest_cells_...` asserts the wording rather
than the old phrase.

### `MAX_NEAREST_CELLS` arithmetic, corrected

At 43.6N a 0.25 degree lattice is about 27.8 km east-west by 20.1 km north-south, so a 50 km radius
admits roughly **fourteen** centroids of the denser grid, not eleven. `MAX_NEAREST_CELLS = 25` and
`MAX_CELL_FANOUT = 250` both still clear it; only the citation was wrong.

### The drought tool answered from an empty table, and succeeded

`drought_history_at_point.sql` read `agri.drought_polygon_snapshot`: **0 rows, no forward
producer anywhere in the tree**. The map serves drought from `geo.drought_areas` — 1,040 rows
across 208 weekly releases spanning 2022-08-09 to 2026-08-11, measured 2026-08-15.

The query therefore *succeeded* and returned nothing, on every call, for every point. There was
no error to notice. The agent could only read the empty result as "the warehouse holds no drought
record here", and would state there was no drought on days the user could see drought painted on
the map in front of them. This is the exact bug class the tool contract's "absence is stated,
never implied" rule exists to prevent, arriving through the one door that rule does not cover: a
tool pointed at the wrong plane.

The fix is to read the plane the map reads, and the test asserts **the plane, not a row** —
`test_the_drought_tool_reads_the_plane_the_map_serves_and_not_the_empty_one`. Sameness of
relation is what makes disagreement impossible rather than merely unlikely; an assertion about
returned values would pass again the next time the source drifted.

Two shape changes came with it, both in the payload and both named in the note:

- **A release that published no drought class over the point is now a row**, with
  `severity_class` null and `covering_class_count` 0. That is a measured "this release existed
  and found no drought here" — a fact. An **empty** `weekly_severity` list is a different claim
  entirely: no release was published in the window at all, so nothing is known either way. The
  old shape collapsed the two.
- **`impact_type` is gone.** `geo.drought_areas` has no such column, and returning a permanently
  null field labelled `impact_type` invites the model to reason about it. `prev_valid_date` and
  `next_valid_date` arrive in its place, from `geo.mv_drought_release_index`, so a day falling
  between two Tuesday releases can be answered with the real gap stated.

## What the agent owes every layer

`docs/layer-lane-standard.md` section 11: a tool must answer at the CALLER-SUPPLIED day (the day the
UI has selected, never `latest`), and every temporal or spatial neighbour must carry its real distance
and its own observation date. Silently substituting a neighbour for an exact answer is the same bug
class as a lane reporting success having written nothing.
