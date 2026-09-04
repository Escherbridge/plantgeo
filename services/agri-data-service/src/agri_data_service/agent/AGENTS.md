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

- **Read-only, in both dialects.** Every statement is a `SELECT` — the DuckDB reads over Parquet
  and the two PostgreSQL statements alike. A writer session is never used.
  `test_every_tool_statement_is_read_only` scans every executable line of both sets, and
  `test_agent_parquet_reads.py` repeats the scan over the DuckDB half so a statement added there
  cannot ship unscanned.
- **Least privilege.** The PostgreSQL session comes from `published_reader_session()`, which
  already falls back to the combined-local session in the local profile the way the other routes
  do. The Parquet reads take one of three process-wide serving slots and open a memory-capped
  DuckDB session inside it — the same admission gate the map's own `/api/v1/parquet` routes pass
  through, so an agent run cannot starve the map. Both are injectable for one run through
  `tools.run_context(session_provider=..., warehouse_source=...)`; that is how the unit suite
  stubs them, and `tests/agent_fakes.py` binds a REFUSING warehouse for the whole module so a test
  that forgets cannot silently read the production bucket.
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

## Reading the Parquet warehouse

**Repointed 2026-09-04** by the `environmental_postgres_retirement_20260904` track, lane C2. Every
environmental answer now comes from the day-partitioned Parquet warehouse the map itself reads.
The reason is unchanged from the previous repoint and is not primarily cost: it is the only
structural guarantee that **the agent cannot contradict the screen**. If the agent answered from
one plane while the map painted from another, the two could disagree about the same cell on the
same day — different quality filters, different day derivation, different refresh moment — and the
agent would state something the user can see is false.

The move was also forced. `geo.mv_signal_cell_daily` was DROPPED against production on 2026-08-18
(6,349 MB, 24,958,092 rows, a 29-minute rebuild) and `agri.spatial_cell` is in the retirement
track's "drop now" class and already absent. Four signal tools were hard-erroring and three more
joined a relation that no longer exists.

| tool | reads |
|---|---|
| `signals_near_point`, `signal_value_on_day`, `signal_neighbors_in_time`, `nearest_signal_cells` | Parquet lane `signal`, `kind=observed`, `zoom=13` |
| `signal_value_on_day`'s second half | `agri.signal_coverage_audit` — **PostgreSQL**, see below |
| `drought_history_at_point` | Parquet lane `drought` (a `release_series`) |
| `fire_history_near_point` | Parquet lanes `fire-detections` and `burn-severity`, plus both availability indexes |
| `forecast_summary_for_cell` | `agri.mv_forecast_ml_daily_serving` — **PostgreSQL**, keyed by a cell resolved from Parquet |
| `observation_coverage_on_day`, `observation_temporal_neighbors` | each surface's published **availability index** |
| `feature_value_near_point` | the surface's own Parquet lane; `interventions` alone stays in `geo.features` |

Three seams carry all of it, and none of them re-implements anything `parquet_ops` already owns:

- `agent/surfaces.py` — the hand-spelled catalogue and the surface→lane table, copied from
  `parquet-slider-capabilities.ts` rather than re-derived.
- `agent/warehouse.py` — day resolution through `parquet_ops.serving.day_status_sets`, bounded row
  reads through `run_serving_read`, coverage through `resolve_availability_lanes`.
- `agent/parquet_reads.py` — the DuckDB statements. They live in Python, not under `sql/agent/`,
  following the convention `parquet_ops/warehouse_reader.py` set: `sql/AGENTS.md` describes a
  PostgreSQL tree loaded through `text()`, and a second dialect in it would be read with the wrong
  grammar.

Consequences that show up in a payload, and therefore in a note:

- **The day is the PARTITION, never a predicate.** `warehouse.scan` is handed exactly the part
  files of the requested day, so no row from a neighbouring day can reach a statement and no
  timestamp is ever cast to a date to keep that true. That is the whole of the named-day rule —
  the one that once moved 6,279 of 16,743 water-gauge rows onto the following calendar day.
- **`min_value` / `max_value` / `avg_value` are reproduced from `normalized_value`, exactly.** The
  Parquet signal schema deliberately omits them: they equalled `normalized_value` on 100% of
  701,257 measured rows and cost 3.81x in file size (RUNBOOK section 0.22.4). `minimum_value`,
  `maximum_value` and `mean_value` therefore mean what they always meant.
  `test_agent_parquet_reads.py::test_the_window_summary_answers_exactly_what_the_dropped_matview_answered`
  compares column by column against a reference written from the deleted statement.
- **`source_parameter` is still gone**, for the same reason as before: the plane's grain is
  `(support_key, signal_name, normalized_unit, cell_id, observed_day)` and carries no upstream
  parameter column. Answers are grained `(signal, support, unit)`.
- **The grid a cell belongs to has no source at all.** `agri.spatial_cell` carried `cell_key`,
  `grid_name` and `resolution_m`; the Parquet plane carries the cell's id and its centroid. Those
  three columns are OMITTED rather than returned null — a permanently null field invites the model
  to reason about it, which is the `impact_type` lesson below — and `nearest_signal_cells` REFUSES
  a `grid_names` filter rather than silently answering unfiltered.
- **`forecast_summary_for_cell` narrowed** exactly as before: ML-method forecasts on series flagged
  `allow_ml_daily_aggregate`, one row per valid day, and deliberately **no fallback** to
  `agri.v_forecast_series_serving`. What changed is only where the cell comes from.

### The two PostgreSQL statements that stay, and why

Neither is environmental data, and the retirement inventory classes both "keep".

`agri.signal_coverage_audit` is the ingest lane's record of what an upstream was asked for and what
it answered. It is the one question Parquet **cannot** answer: a governed-absence marker settles a
whole lane-day, while this ledger is grained by signal, cell and fetched window and says *why*
nothing landed for one of them. It used to resolve "cells near the point" from `agri.spatial_cell`;
the cells now arrive as two positionally-paired arrays resolved from the Parquet plane by the same
call that read the values, which is STRICTER than the join it replaces — the audit is read over
exactly the cells the answer came from rather than every cell the radius admitted.

`agri.mv_forecast_ml_daily_serving` is the governed ML serving plane, built on
`agri.v_forecast_series_serving` and inheriting its published/finalized/validated gate. It keeps
its `pg_class` probe (below) because that gate is the reason an agent cannot quote a draft.

`geo.features` keeps exactly one agent reader: `interventions`. RUNBOOK section 0.26.1 keeps that
lane in PostgreSQL because it is community data a user writes rather than environmental data an
upstream publishes, so it has no registered Parquet lane and inventing one would be a fiction.

### Refusing: two states became four

The refusal discipline did not soften in the move; it gained states. A PostgreSQL matview could
only be built or unbuilt. A Parquet lane-day is in one of four states, and three of them are things
a model must never collapse into "nothing is here":

| state | what it means | what the model may conclude |
|---|---|---|
| `published` | the day holds rows and they were served | the rows, and nothing beyond them |
| `governed_absence` | the lane looked and the SOURCE had nothing; the marker says why | a measured absence, quoting the recorded reason |
| `day_not_written` | nobody has ever written this day | **nothing at all** |
| `lane_never_written` | the lane has written nothing at this rung, ever | **nothing at all** — this is the old "unbuilt plane" |

`day_state` travels in every day-scoped payload and the notes tell the model to read it BEFORE the
rows. Above those four sit the SERVING refusals (`parquet_ops.faults`), which are statements about
this process and never about the warehouse: a half-written export, a read past its memory ceiling,
every serving slot busy. Each carries its own `refusal_code` so it cannot be folded into an absence.

Two refusals this module adds on top:

- **`lane_columns_absent`.** MEASURED 2026-09-04: the newest published z13 signal part
  (`year=2026/month=08/day=06/part-0.parquet`) carries eleven columns and NEITHER `cell_longitude`
  NOR `cell_latitude`, although `warehouse/parquet/schema.py` declares both non-nullable. The lane
  was exported before the positions were added and the `postgres-*` lanes are stopped, so no
  re-export has followed. Without a probe every signal tool answers a `duckdb.BinderException` —
  an unexplained tool error, exactly what this discipline exists to prevent. `warehouse.scan_all`
  therefore checks the required columns once per read and refuses by name, saying the lane owes a
  re-export. **Until that re-export lands the four signal tools refuse rather than answer**, which
  is the honest state and is strictly better than the hard error they returned before.
- **`parquet_availability_withheld`.** Coverage is answered from each lane's published availability
  index — one pointer GET plus one bounded generation GET. A lane that cannot prove itself is
  REFUSED rather than filled in from an object listing: that listing is the whole-stream LIST the
  track's A4 tripwire forbids on a request path, and a lane answered from different evidence than
  the map used could disagree with the slider about the same day. While
  `PARQUET_COVERAGE_AUTHORITY` is `census_until_bootstrap` these two tools refuse for every lane;
  they light up as each lane's index is published.

The `pg_class` probe survives for the one relation left that needs it, and its answers are still
cached per `run_context`. It is **not** a freshness test and nothing may read it as one: a matview
refreshed once and then frozen reports `relispopulated = true`. That gap is why
`geo.mv_signal_observation_day` was removed from the probe list rather than left in it — its
refresh was dropped from the spec in `f5510a1` after timing out at 302 s against a 300 s
`statement_timeout`, so it is populated AND frozen, and a probe that passed it would have let
`observation_coverage_on_day` and `observation_temporal_neighbors` serve stale census answers with
no refusal at all. The census question moved to the availability index, which carries a
`source_ceiling_day` and can therefore say how current it is.

### Window caps are scan budgets, and they fell

Against an index range a decade-deep window was a longer contiguous scan. Against Parquet it is one
object-store GET per written day per lane, so `MAX_DAYS_BACK` fell from 3,650 to 92,
`MAX_WEEKS_BACK` from 520 to 52 and `MAX_FIRE_YEARS_BACK` from 45 to 2. **These numbers are chosen,
not measured**, and every note that reports one says it is a scan budget rather than the depth of
the record — the depth question moved to `observation_coverage_on_day`, which answers it for a
lane's whole history from two small GETs.

A second guard sits under them: `MAX_SCANNED_DAY_PARTITIONS` (120). Where a window still holds more
written days than that, the read is narrowed to the NEWEST ones and the narrowed span is echoed as
`scanned_from` / `scanned_through` beside the requested one. Answering two years from four months
of it, silently, is the fabricated-absence bug in another costume.

### Two ordinate conventions, and one of them lies

Recorded here because it is the single most dangerous thing in `parquet_reads.py`. DuckDB's
GEOMETRY functions take `ST_Point(longitude, latitude)`; its GEODESIC DISTANCE functions take the
first ordinate as the LATITUDE. Measured against DuckDB 1.5.4 on 2026-09-04:

```
ST_Distance_Spheroid(ST_Point(43.6, -116.2), ST_Point(43.62, -116.25)) = 4607.70 m   correct
ST_Distance_Spheroid(ST_Point(-116.2, 43.6), ST_Point(-116.25, 43.62)) = NaN         refused
ST_Distance_Sphere(  ST_Point(-116.2, 43.6), ST_Point(-116.25, 43.62)) = 5645.93 m   WRONG
```

Every distance therefore uses `ST_Distance_Spheroid` and `ST_Distance_Sphere` is banned outright:
fed the ordinates backwards the spheroidal function answers NaN, while the spherical one answers a
plausible number 23% too large. A distance the model quotes beside a reading has to be wrong loudly
or not at all. `ST_Distance_Spheroid` is also the exact analogue of the retired statements'
`::geography` distance — both are WGS84 ellipsoidal — so this is a reproduction, not an
approximation. `test_agent_parquet_reads.py::test_the_probe_point_is_bound_latitude_first` pins all
three numbers.

DuckDB has **no geodesic distance to a polygon edge** — both geodesic functions accept POINTs only
— so there is no `ST_DWithin(geography)` to reproduce for a polygon lane. Two honest departures
follow, both visible in the payload: membership is decided by the metre-accurate BOX rather than the
circle inside it (a corner feature up to √2 × radius away can appear, and `search_shape` says so),
and the distance reported is to the feature's CENTROID (`distance_basis: "centroid"`).
`covers_probe_point` is exact and answers the question a polygon is usually asked.

## The generic surface triad

Section 11 of `docs/layer-lane-standard.md` obliges *every* layer to answer three questions, and
only the signal plane had all three. Eleven more bespoke tool sets would repeat the mistake the
pre-aggregation work exists to undo — many relations answering one question — so instead there
are three tools parameterised by `surface_name`, reading the same relations the app reads:

| tool | question | source |
|---|---|---|
| `observation_coverage_on_day` | is this day covered at all, and where does it sit in the surface's history | the surface's published availability indexes |
| `observation_temporal_neighbors` | nearest covered day each side, with `distance_days` | the same indexes, already in memory |
| `feature_value_near_point` | nearest published features on that day, with `distance_meters` | the surface's own Parquet lane |

A surface backed by SEVERAL lanes is covered only on days EVERY one of them published — air
temperature is three lanes (mean/max/min), soil moisture three depths, soil temperature four — for
the same reason `parquet-slider-capabilities.ts::commonPublishedRanges` intersects rather than
unions. A day one depth is missing is a day the map cannot draw, and reporting it covered would put
the agent one step ahead of the screen. `lane_states` names each lane's own verdict so the missing
one can be pointed at.

`AGENT_SURFACE_NAMES` holds **24 hand-spelled names** — 11 `geo.layers` rows, 4
`SLIDER_STREAM_LAYER_NAMES`, 9 `climate-field-<signal>` streams — for the same reason
`docs/layer-lane-standard.md` §9 requires a hand-spelled catalogue assertion: a derived list
drifts with the thing it is meant to check. If this were built from a query, a layer that
vanished from the database would vanish from the agent's vocabulary too, and the agent would say
"I do not know that surface" instead of "that surface stopped being served".

Two design points that are load-bearing rather than incidental:

- **An uncovered day is answered three ways, not one.** `observation_coverage_on_day` returns
  the surface's earliest and latest published days beside the verdict, and the lane's own
  `source_ceiling_day`, so the model can say *before this lane's published history* / *past what
  the source could have published* / *a real hole in the middle* rather than merely "empty". Those
  are three different facts and only one of them is a bug.
- **Refusals name the gap.** An unknown surface, a stream handed to `feature_value_near_point`, a
  surface with no Parquet lane, a lane that cannot prove its coverage and a lane that never wrote
  anything all produce a typed refusal listing what *is* answerable — never an empty result, which
  the model reads as an absence.

### The bounding-box prefilter

A metre radius has to become a degree box before it can be a range predicate DuckDB pushes into a
Parquet row group. The exact geodesic test runs on the survivors, so the box changes how many rows
are measured and never which rows survive — the same relationship `geom && ST_Expand(...)` had to
`ST_DWithin(geography)` on the PostgreSQL side, for the same reason.

The box is sized **per axis** (`_bbox_bounds`). A degree of latitude is a fixed 110,574 m; a degree
of longitude is 111,320 m only at the equator and shrinks by `cos(latitude)`. Sizing the box on the
latitude figure alone clips its east–west edges away from the equator and silently drops real rows,
which is exactly the failure a prefilter must not introduce. `_bbox_degrees` — the square form,
sized on the wider axis — survives for the one PostgreSQL statement that still takes one.

## Answering at the selected day

Three tools exist purely to satisfy §11 of the layer-lane standard for the signal plane, and
they are a different shape from the four above: those summarise a *window* and are free to
answer from whichever days inside it happen to hold readings; these answer about **one day, the
day the map is showing**.

| tool | question | statements |
|---|---|---|
| `signal_value_on_day` | what was measured on this exact day | `SIGNAL_DAY_VALUES` + `SIGNAL_ADMITTED_CELLS` + `signal_coverage_on_day.sql` |
| `signal_neighbors_in_time` | what is the nearest reading each side of it | `SIGNAL_TIME_NEIGHBORS` |
| `nearest_signal_cells` | where are the measurements, and how far | `SIGNAL_CELL_DAY_COUNTS` |

Design rules, each of which has a test:

- **`day` is required, and it is a string.** No default. A defaulted day is exactly how a tool
  drifts back to "latest" and starts answering a different question than the one asked. It is
  `str` rather than `date` because the signature *is* the published JSON schema; it is parsed
  with `date.fromisoformat` and an unparseable value is **refused**, never replaced with today.
  Substituting a date is the same refusal MTBS makes for a fire year with no dated release.
- **The day is the partition, not a filter.** The read is handed exactly the part files of the
  requested day, so a neighbouring day's rows cannot reach the statement and no timestamp is cast
  to a date to keep that true. The half-open pair of UTC midnights survives in exactly one place —
  `signal_coverage_on_day.sql` — because `agri.signal_coverage_audit` is grained by the *window a
  lane fetched* rather than by a day, and overlap is the only honest test for that.
- **Every proximity answer carries its distance and the observation's own date.** Temporal rows
  carry `observed_day`, `nearest_cell_observed_at`, signed `day_offset` and magnitude
  `distance_days`; spatial rows carry `distance_meters` and the centroid coordinates. A
  neighbour handed back without its gap is indistinguishable from an exact match, which is the
  same class of bug as a lane reporting success having written nothing.
- **`nearest_signal_cells` LEFT JOINs its day counts.** An INNER join would drop cells holding
  nothing, and "the nearest cells" would silently mean "the nearest cells that had data" — the
  substitution the tool exists to expose. A cell with nothing comes back with a count of `0`.
  **The cell list is OBSERVED, not declared.** `agri.spatial_cell` was the registry that answered
  "which cells exist here" and it is gone, so the universe is now the cells that reported at least
  once in `CELL_UNIVERSE_DAYS` (30) before the requested day. A cell silent longer than that is
  missing from the list, and the note says so outright rather than letting an observed set read as
  a grid.
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

- `signal_value_on_day` issues **three** statements for one tool call — two DuckDB reads inside
  ONE admitted session (the values and the admitted cells) and one PostgreSQL read (the absence
  ledger). One session, because a tool asking two questions of one day should not queue twice
  behind the three-slot serving gate, and because the second statement then reads part files this
  process has already opened. `test_every_tool_statement_is_read_only` drives all ten published
  tools and asserts `len(WAREHOUSE_TOOLS) == 10` beside the statement set: the tripwire scans every
  statement the model can reach in either dialect, and a count alone would let an eleventh tool
  ship unscanned. The plane probe is excluded and asserted separately, because it is cached per run
  rather than issued per tool.
- `fire_history_near_point` and `observation_coverage_on_day` issue **one read per lane**, because
  a surface can be several lanes. `fire_history_near_point` also asks each lane's availability
  index for its whole-lane history, which is two small GETs and not a scan.
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

Parquet columns come from the REGISTERED Arrow schemas under `warehouse/schemas/`, and which
column carries a lane's position is decided by `parquet_ops.warehouse_reader.spatial_support` —
imported, never re-derived — so the agent and the map agree about where a lane's coordinates live.
A lane declaring neither a coordinate pair nor a WKB column is refused rather than answered for the
whole world. `feature_value_near_point` returns the lane's own typed columns under `properties`;
there is no JSON allow-list any more because there is no JSON blob to guard — the ~1,467 MB of
TOAST across 4.97 million `geo.features` rows that made `FEATURE_PROPERTY_KEYS` necessary is not a
property of a Parquet lane. That allow-list survives, trimmed, for `interventions` alone.

`fire_history_near_point`'s lanes are spelled in `surfaces.py::FIRE_LANE_NAMES` rather than
resolved through `ingest/firms.py` and `ingest/mtbs.py` as the PostgreSQL statement did: those
resolvers answer with a `geo.layers` row name, and a Parquet lane slug is a different namespace
that happens to agree today. `FIRE_LANE_FEATURE_COUNT_COLUMN` is hand-spelled beside it, because
the two lanes have genuinely different grains — `fire-detections` publishes one row per CELL-DAY
carrying `detection_count`, `burn-severity` one row per mapped perimeter — and guessing from a
column name would be a rule nobody wrote down.

The four PostgreSQL statements that remain live in `sql/agent/*.sql` behind `load_query_sql`, with
the beginner-doc header standard from `sql/AGENTS.md` — including its bind-param trap: parameter
names in comments carry no leading colon, because `text()` scans comments too. The eight that
moved were DELETED, not left orphaned: a `.sql` file with no call site fails
`test_sql_tree_conventions.py::test_loaded_exactly_once`, and
`test_agent_parquet_tools.py::test_the_agent_sql_tree_holds_only_the_four_statements_that_stay`
asserts the surviving set by name.

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

`observation_count_on_day` counts rows on the GOVERNED SIGNAL plane only, while the tool returns
cells from every grid that plane knows -- including `sentinel2-ndvi-0p25deg`, whose NDVI lands
elsewhere. The note previously read "0 is an answer, not an omission", which is section 3's named
failure: a census over one plane reporting healthy lanes as dead. Both the note and the
model-facing docstring name the plane. The 2026-09-04 repoint added a second thing a 0 can mean and
the note names that too: on a `governed_absence` or `day_not_written` day EVERY count is 0 for a
reason that has nothing to do with the cells, which is why `day_state` must be read first.

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
`test_the_drought_tool_reads_the_lane_the_map_serves_and_not_the_empty_one`. Sameness of source is
what makes disagreement impossible rather than merely unlikely; an assertion about returned values
would pass again the next time the source drifted. The test survived the 2026-09-04 repoint
unchanged in intent: the map now serves drought from the Parquet `drought` lane, so the tool reads
that, and the assertion still names the source rather than a value.

Three shape decisions came with it, all in the payload and all named in the note:

- **A release that published no drought class over the point is a row**, with `severity_class`
  null and `covering_class_count` 0. That is a measured "this release existed and found no drought
  here" — a fact. An **empty** `weekly_severity` list is a different claim entirely: no release was
  published in the span at all, so nothing is known either way. The old shape collapsed the two.
  Against Parquet the two halves come from one pass: `count(*)` over the release's rows gives
  `published_class_count`, and three `FILTER (WHERE covers_probe)` aggregates describe only the
  polygons over the point.
- **`impact_type` is gone.** Neither source has such a column, and returning a permanently null
  field labelled `impact_type` invites the model to reason about it. That lesson is why the retired
  cell registry's `grid_name` and `resolution_m` are omitted from `nearest_signal_cells` rather
  than nulled.
- **`prev_valid_date` and `next_valid_date`** arrive in its place, so a day falling between two
  Tuesday releases can be answered with the real gap stated. They came from
  `geo.mv_drought_release_index` and now come from the LISTING — which is why the lane is listed
  one year below the requested window, so the OLDEST release in the answer can still name the one
  before it.

### What the 2026-09-04 repoint could NOT move, and what that costs

Recorded here rather than discovered later. None of these is a defect in the port; each is a state
of the warehouse the port made visible.

- **The signal lane owes a re-export.** Its published z13 parts carry no `cell_longitude` /
  `cell_latitude`, so the four signal tools cannot resolve a cell spatially and refuse with
  `lane_columns_absent`. They answer the moment the lane is re-exported through the current
  schema; nothing in this package needs to change.
- **Coverage is withheld until the availability indexes are bootstrapped.** Under
  `PARQUET_COVERAGE_AUTHORITY=census_until_bootstrap` no lane publishes one, so
  `observation_coverage_on_day` and `observation_temporal_neighbors` refuse. The alternative — a
  whole-stream object listing on a request path — is what the track's A4 tripwire forbids.
- **`cell_key`, `grid_name` and `resolution_m` are gone for good.** They were `agri.spatial_cell`
  columns and that relation is dropped. `nearest_signal_cells` reports `cell_id` and refuses a
  `grid_names` filter; `forecast_summary_for_cell` reports `cell_id` where it reported `cell_key`.
- **`observation_coverage_on_day`'s census-specific columns have no twin.** `unlinked_count`,
  `distinct_key_count` and `metric_counts` were properties of `geo.v_observation_day_census`.
  `surface_kind` became `lane_nature` (`daily_series` / `release_series` / `static_lookup`), and
  `newest_observed_at` became `published_at` — a publication instant, which is a different fact
  from an observation instant and is named differently for that reason.
- **A polygon lane's `distance_meters` is to its centroid**, because DuckDB has no geodesic
  distance to an edge. `distance_basis` says which measurement it is, on every row.
- **`interventions` still reads `geo.features`.** It is community data, it is empty, and RUNBOOK
  section 0.26.1 keeps it in PostgreSQL. It does not block the `geo.features` drop packet any more
  than the layer itself already does.

## What the agent owes every layer

`docs/layer-lane-standard.md` section 11: a tool must answer at the CALLER-SUPPLIED day (the day the
UI has selected, never `latest`), and every temporal or spatial neighbour must carry its real distance
and its own observation date. Silently substituting a neighbour for an exact answer is the same bug
class as a lane reporting success having written nothing.
