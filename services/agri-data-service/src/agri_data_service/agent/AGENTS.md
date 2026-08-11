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

Ambient state (the session provider, the per-run tool ledger) travels in `ContextVar`s
because a tool function's signature *is* its model-facing schema — a `session` parameter
would become something the model is asked to supply.

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
  statement, so `test_every_tool_statement_is_read_only` drives all seven published tools and
  asserts `len(WAREHOUSE_TOOLS) == 7` beside the statement count. Both halves must be edited to
  add a tool, which is the point: the tripwire scans every statement the model can reach, and a
  count alone would let an eighth tool ship unscanned.

### Where the columns come from

Six of the seven tools read `agri.*` and every column is verified against
`models/historical.py`, `models/forecasting.py` and the declarative view
`db/agri/views/v_forecast_series_serving.sql`. `forecast_summary_for_cell` reads that
serving view rather than re-deriving its joins, because the view is what encodes "published,
finalized, validated" — the agent must not be able to quote a draft forecast.

`fire_history_near_point` is the exception: fire evidence is served from `geo.features`,
whose declarative source of truth is the Next.js Drizzle schema
(`src/lib/server/db/schema.ts`), not this service's ORM. Only columns that schema declares
are referenced (`id`, `layer_id`, `geom`, `properties`, `status`). The layer names come from
`ingest/firms.py` and `ingest/mtbs.py` via their existing call-time resolvers rather than
being re-spelled here, so a renamed layer moves in one place.

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

## What the agent owes every layer

`docs/layer-lane-standard.md` section 11: a tool must answer at the CALLER-SUPPLIED day (the day the
UI has selected, never `latest`), and every temporal or spatial neighbour must carry its real distance
and its own observation date. Silently substituting a neighbour for an exact answer is the same bug
class as a lane reporting success having written nothing.
