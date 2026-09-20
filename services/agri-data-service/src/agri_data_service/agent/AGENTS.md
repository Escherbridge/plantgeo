# The location-analysis agent graph

## Selection evidence and RAG contract (2026-09-20)

`list_environmental_layers` discovers every map surface, including non-slider botanical,
community and soil-raster variants. `surface_evidence_for_selection` is the numeric retrieval
entry point shared by MCP, the HTTP tool bridge and both agent workflows. Older radius tools
remain internal compatibility helpers where needed; they are not model-facing choices.

The request contains the selected coordinate, map zoom, exact day and inclusive active calendar
window. Its spatial scope is the Web Mercator tile containing that coordinate. The serving rung
follows the map's zoom ladder. `selection_scope.support_lattice` mirrors `servedCellLattice` in
`src/lib/map/zoom-tiers.ts`: one-degree NASA POWER support, quarter-degree soil/NDVI support,
their distinct phases, and the derived rung's snap correction. A climate cell whose centroid is
farther than the former 50 km radius still answers when its support contains the selected point.
Point support must intersect the tile; polygon support uses exact geometry intersection.
Distances, containing-support flags, support bounds and original numeric properties remain
explicit. Raster colors are never treated as measurements.

`selection_reads` runs the map's ordinary `resolve_day`/`resolve_release` against the same
availability-authorized inventory and receipt-verified bytes. There is no frozen snapshot
fallback and no generic observation-plane query. Release rows are cached inside a lane read
when several comparison dates resolve to the same published version. Exact part-file identity
survives custom SQL. Static version dates and collection publication dates are never daily
observation dates. Refusals are retained per lane/day and do not erase other depth/metric results.

Each call retains the exact selected day and limits additional history to three days for
one or two lanes, or one day for three or four lanes. The shared page budget admits at most
eight lane-day resolutions including selected-day reads; cold multi-depth requests must fit
the ordinary tool deadline. The history schedule starts with both active-window endpoints,
then the selected day and actual published neighbours on both sides. Subsequent pages distribute
reads across each lane's published dates before visiting unwritten calendar gaps. The plan uses
the same authorized inventory as subsequent reads; integer `page_start` offsets eventually cover
every requested day without omissions. The response names its effective `days_per_page`, sampled
days, full requested count, next offset and completeness. The catalogue reports the maximum budget.
An incomplete page supports a sampled comparison, never a complete trend or absence on unread
days. Up to 36,600 calendar days may be requested without silently narrowing to newest data.

App-owned community and raster surfaces use the bounded `/api/v1/map-evidence` proxy only when
`AGENT_MAP_APP_URL` is configured. Missing configuration is a typed refusal. Botanical occurrence
evidence pins one published generation and distinguishes collection-event time from publication
time. Exact collection dates use `observed_day`; imprecise dates retain `observed_interval`, while
the current release retains its actual `published_at`. Collection dates never become a synthetic
`served_day`. Richness and collecting-effort layers expose the actual current aggregate under
`snapshot_context` and explicitly refuse historical publication claims rather than pretending
that precomputed current cells were filtered to the selected date.

Regional climate and soil gates bind their concrete surface names to NASA POWER and
ERA5-Land respectively, so availability never depends on a retired generic layer binding.

The older sections below record previous contracts and migration evidence. This selection
contract and the executable registry govern current model-facing retrieval.

## Live regional agent tool bridge (2026-09-12)

The existing Next.js regional agent now reads the tool registry and executes environmental
tools through the provider-independent `routes/agent_tools.py` boundary. It retains its current
model provider while using the same bounded readers as this Python graph and MCP surface.
`surface_evidence_for_selection` reads each climate/soil/feature surface's own declared Parquet lanes
at the selected coordinate's tile and map zoom. Multi-depth
and multi-metric surfaces keep one result and day state per lane, including unwritten lanes.
The returned original properties, served day, distances and geometry-distance basis prevent a
nearby cell or historical observation from becoming an asserted local measurement.

### Snapshot reads

`surface_value_near_point` uses each registered lane's nature. Daily lanes address the exact
selected partition; `static_lookup` and `release_series` lanes use the map's `resolve_release`
rule, with its bounded twelve-year lookback. Both requested and served dates remain explicit on
each lane and feature. A snapshot version date does not become a daily observation date.

The shared resolver is synchronous while the agent's admitted proximity reader is asynchronous.
`warehouse.release_rows` suspends the resolver at its row-read boundary, runs the existing bounded
proximity query, then resumes resolution against the same cached inventory and snapshot metadata.
The second pass validates the actual returned MTBS identities against the map's snapshot proof;
an empty planning result never stands in for the real rows. Production injects the same verified
MTBS loader as the map. The adapter changes only the resolver's final existence probe: it stops
at the first tier layout key instead of materializing an entire tier. Daily missing-day probes
use the same early stop. No stream census or PostgreSQL fallback participates in these reads.

The public selection reader requires the exact selected day and active range. It preserves the
inclusive requested window across balanced history pages and reports continuation explicitly.
A sampled page cannot establish a complete multi-year trend. The map-facing bridge catalogue excludes only `species_information`, whose
separate authoring endpoint owns canonical-UUID access.

The map's location-analysis agent, rebuilt server-side where it can sit directly on the
warehouse. It eventually replaces the hand-rolled loop in
`src/lib/server/services/ai-prompt.ts`; until the Next.js side switches endpoints, that file
remains the live implementation and the **authority on the product surface** — the stream
event union, the report field names, and the enum vocabularies all come from there and from
`src/lib/regional-intelligence.ts`.

Report claims optionally carry up to eight `evidenceReadIds` referencing the server-produced
read ledger. IDs retain the cited read's date and regional scope through the frontend report
and export; they never author or replace the ledger. The Python projection bounds each ID and
omits the optional field during serialization when it is absent, preserving older report payloads.

## Current serving boundary

Environmental climate, soil, vegetation, fire and water observations come from their declared
governed serving lanes. Missing or unwritten products return typed refusals; they never trigger a
PostgreSQL observation fallback. App-owned overlays and public reference layers use their existing
map readers through the bounded authenticated app bridge. Those adapters preserve the selected day
and disclose when their data is current-only or cannot answer that day.

The retired generic agent tool vocabulary and its deletion evidence are recorded in
`conductor/tracks/repository_conformity_hardening_20260901/signal-tool-retirement.md`.
The single `WAREHOUSE_TOOLS` registry exposes catalogue discovery, selection evidence, metadata,
botanical evidence, and the caller-scoped species lookup.

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

| Distinct measured surfaces that returned rows | Verdict | Search budget |
|---|---|---|
| 0 | insufficient | 3 (`MAX_SEARCHES_PER_REQUEST`) |
| 1 | insufficient | 2 |
| 2+ **and** the caller asked a specific question | external guidance allowed | 1 |
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

- **Read-only, in the warehouse dialect.** Every environmental statement is a DuckDB read over
  Parquet. A writer session is never used; the session provider exists only for the species/profile
  lookup.
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
- **Bounded, with the bound reported back.** The selection's tile, calendar range, map zoom,
  history page, row cap and continuation are explicit. A page never claims to contain the entire
  history until the continuation contract says it does.
- **Numeric support, with provenance.** Read the underlying map data, preserving original properties,
  source support, selected and served dates, and any spatial or temporal distance. A tile color is
  not a measurement.
- **Absence is stated, never implied.** Every payload carries a `note` distinguishing "the
  warehouse holds no record here" from "the condition is absent". The system prompt repeats
  the rule; the payload makes it unavoidable.

Ambient state (the session provider, the per-run tool ledger, the per-run plane-probe cache)
travels in `ContextVar`s because a tool function's signature *is* its model-facing schema — a
`session` parameter would become something the model is asked to supply.

The botanical `species_information` tool is scoped to the warehouse pass. Its exact UUID is bound
by `tools.run_context`, while the optional web pass runs after that context exits. The graph therefore
uses `WAREHOUSE_TOOLS_FOR_WEB`, which omits `species_information`, instead of re-exposing a tool
whose UUID constraint has expired. If a later pass needs botanical access, it must re-enter the same
context with the caller's UUID and add a regression for mismatched and omitted UUIDs.

## Reading the Parquet warehouse

`surfaces.py::SURFACE_PARQUET_LANES` names each product's lanes. Multi-depth and multi-metric
surfaces keep separate lane results so a missing depth is visible. The selection reader uses the
same published zoom rungs and availability-authorized support as the map.

A published partition, a governed upstream absence, an unwritten day, and a never-published lane
are different states. A refusal, missing column, exhausted admission budget, or unavailable index
does not establish environmental absence. The shared warehouse probes required columns before
running SQL and carries the receipt-bound evidence source into the read.

The request uses bounded serving admission and memory-capped DuckDB sessions. Partition keys are
bound as values, never interpolated SQL. Hive partitioning stays disabled so path segments cannot
masquerade as data columns. Product-specific source columns survive in the returned properties.

DuckDB geometry takes longitude then latitude; `ST_Distance_Spheroid` takes latitude then
longitude. Both conventions are pinned by real DuckDB tests. Polygon centroid distance is labelled
as centroid distance and never presented as distance to the polygon edge.

## The catalogue the agent and the map share

`list_environmental_layers` publishes all selectable map data surfaces, aliases such as VPD and
NDVI, reader types, declared lanes, and region admission. The vocabulary remains explicit so a
removed lane does not silently erase a product from discovery.

`observation_coverage_on_day` and `observation_temporal_neighbors` are metadata tools: they
answer which publication dates exist, not numeric values. `surface_evidence_for_selection` is
the model-facing numeric reader for every surface. Retained product-specific query functions support
internal contracts but do not provide alternate model-facing defaults.

## Answering at the selected day

The exact selected date is required by the numeric tool. Its active calendar range and time scale
are separate arguments, so a user viewing an older year cannot silently receive recent history.
The first history page balances dates through the requested interval; subsequent pages complete
the requested range without presenting unsampled days as absent.

Daily products use the requested partition. Release and static products preserve both the
requested day and the applicable release date, never selecting a later publication. Temporal
neighbors retain their own dates and offsets. Spatial neighbors retain their support and distance.
A grid cell containing the coordinate is still a cell measurement, not a point measurement.

## Report vocabulary

`report.py` mirrors `ai-prompt.ts`'s `REPORT_TOOL` **field for field**, camelCase included:
`riskSummary{level, headline, factors, evidenceOrigin, evidenceSources}`, `observations[]`,
`remediation[]`, `professionalConsultation`. The enum members are the Python projection of
`src/lib/regional-intelligence.ts`, which stays the single definition; drift is a contract
break, and `test_report_rejects_a_vocabulary_the_frontend_cannot_render` is the tripwire.
The camelCase field names carry a file-level `ruff: noqa: N815` for exactly this reason —
these are wire names, not Python identifiers we are free to restyle.

Report claim sources also accept the 39 map data surface names through
`RegionalClaimEvidenceSource`. The nine initial-context freshness keys remain a separate
contract; accepting a surface citation does not create a timestamp or affirm a measurement.
The TypeScript live flow attaches its optional server evidence ledger after model report
validation. Historical reports without that ledger remain valid and retain their original data.

`disclaimer` and `citations` are deliberately *not* fields of the report model, matching the
TypeScript split: the disclaimer is a constant (`AI_GENERATED_DISCLAIMER`, sent once as the
stream's first frame) and citations ride the `sources` event. Putting either inside the
model would invite the model to paraphrase a legally load-bearing sentence, or to invent a
citation to fill a required field.

The report is produced by **structured outputs** (`client.beta.messages.parse` with
`output_format=RemediationReport`), not by forcing a report tool on the last round the way
the TypeScript side does. Same guaranteed shape, one fewer moving part, and the failure mode
is a validation error rather than a plausible-looking tool call with a missing field.

## A layer this region binds no source for

`federation.md` §2's last bullet, on the agent surface. A tool for an unbound layer **stays
registered** and answers `not_available_in_region` (`tools.py::_region_absence`) naming the layer
slugs and the region's slug and display name.

Registered, not removed, because the vocabulary is the platform's and not the deployment's: dropping
the tool would make the model answer "I do not know that surface" for a layer PlantGeo does have,
and the model cannot tell that apart from a typo in the surface name. The refusal wording follows
the three refusals above it — it opens "This is a REFUSAL, not an absence" and forbids the four
readings the model reliably reaches for (absent, zero, unaffected, a gap in the record). What makes
it different from `parquet_lane_never_written` is scope: a never-written lane might be written
tomorrow, whereas an unbound layer has no day, location or filter that would ever answer here.

Asked **before** the lane question at every call site, because a layer this region binds no source
for has no lane question. The mapping from a catalogue surface to its manifest layer is
`surfaces.py::SURFACE_REGION_LAYER_SLUGS`, hand-spelled for the same reason the two tables above it
are: `drought-areas` binds through `drought`, while each climate and soil surface binds its own
name to `nasa_power` or `era5_land`. No product inherits a generic environmental binding.

The web half of the same fact rides `/api/v1/parquet/coverage` as `layer_bindings`
(`parquet_ops/wire.py::LayerBindingCoverage`), so the slider catalogue and the legends say the same
sentence the agent does.

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

## The MCP tool surface

`WAREHOUSE_TOOLS` has two consumers, and only one of them is the graph.

`species_information` is the nonspatial exception in that registry. It uses the same ambient
read-session provider as the graph and MCP session, but its ledger entry always has `row_count=0`:
authoring reference data is not location-warehouse coverage. Sufficiency counts distinct measured
surfaces and excludes species, catalogue, publication and coverage metadata. Its exact UUID input,
unpublished posture, field missingness, approved-only companion
filter, and refusal limits are service-enforced rather than prompt-only.
Graph runs additionally bind the tool argument to the optional canonical UUID validated at HTTP
ingress; without that field the graph refuses botanical reads. Direct MCP callers remain responsible
for supplying an exact UUID and receive the same bounded response contract.

The graph is an opinionated consumer: it decides in Python whether the public web is warranted,
budgets searches, and forces a structured report. `agent/mcp_server.py` is the unopinionated one.
It lists the same six tools over MCP stdio and calls them, carrying none of that policy, because a
second quiet copy of the sufficiency gate is the way the two surfaces start disagreeing.

Neither surface owns a schema. `agent/llm.py::tool_schemas` reads `.name`, `.description` and
`.input_schema` off the `beta_async_tool` objects themselves and renders the OpenAI
`tools[].function` shape; `mcp_server.tool_descriptors` renames one field to MCP's `inputSchema`. A
parameter added to a tool therefore appears on both surfaces without either being edited, and the
docstring that already documents the caps and the four-state contract is what the model reads.

**The provider is a second, separate credential.** `ANTHROPIC_API_KEY` drives `/agent/analyze`;
`AGENT_LLM_*` drives the MCP surface's CLI harness. They are not interchangeable in either
direction: `graph.py` calls `client.beta.messages.tool_runner` and `client.beta.messages.parse`
with `output_format=`, and an OpenAI chat-completions endpoint (OpenRouter, LM Studio, vLLM)
implements neither. `agent/llm.py` reimplements only the part the tools need — publish schemas,
receive `tool_calls`, execute, post results back — on the `httpx` this service already depends on,
because adding `openai` or `mcp` means a `uv sync` this toolchain does not tolerate on an unrelated
change. `require_agent_llm()` refuses by variable name, `AGENT_LLM_BASE_URL` must be
credential-free, and plaintext `http` is allowed only on the loopback interface.

`agent mcp-serve` and `agent list-tools` need **no** provider key at all. The MCP surface is the
tools; the model is the client's problem.

### stdout is the transport, and something else was writing to it

Measured twice, once in each direction. Half this service's modules log through a bare
`structlog.get_logger()`, whose unconfigured default factory prints to **stdout** — and
`parquet_ops/availability_coverage.py`'s `availability_census_fallback` warning landed as line four
of a live MCP session, between two protocol frames, and as line one of `agent ask`'s JSON. Both
consumers' parsers die on it.

`mcp_server.reserved_stdout` takes the real stream, rebinds `sys.stdout` to stderr for the duration,
and forces UTF-8 on the stream it kept. Every logger, library and stray `print` beneath a tool call
is therefore harmless, which is a guarantee that fixing individual loggers cannot give. Both stdio
surfaces use it: the transport and every CLI leaf that prints machine-readable JSON.

### Running it

`.mcp.json` at the repo root registers the server with `cwd: services/agri-data-service`, and the
cwd is load-bearing: `Settings` reads `env_file=".env"` relative to the working directory, so a
server launched from the repo root reads the wrong env file and finds no provider — and no bucket.

```
agri-service agent list-tools                 # schemas only; needs nothing running
agri-service agent probe                      # authenticates; reads no lane
agri-service agent call-tool --name ... --arguments '{...}'   # one tool, no model
agri-service agent ask --longitude ... --latitude ... --question '...'
agri-service agent mcp-serve                  # stdio transport
```

`--max-tokens` on `ask` is not decoration. Omitting an output ceiling is not "no limit", it is the
model's own maximum, and a gateway prices the request against it up front: OpenRouter answered
`HTTP 402: you requested up to 64000 tokens, but can only afford 42853` on a question whose real
answer was a few hundred tokens.

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

### The selected day and active range reach the route together

`AgentAnalyzeRequest` validates the selected day and calendar range before they become
`AgentRequest` context. The Next.js caller forwards the visible selected day, active time scale,
calendar bounds, map zoom, and per-layer settled dates. A later model tool call cannot widen or
replace that context. Tests cover ingress, prompt propagation, and selection-bound tool arguments.

## What the agent owes every layer

`docs/layer-lane-standard.md` section 11: a tool must answer at the CALLER-SUPPLIED day (the day the
UI has selected, never `latest`), and every temporal or spatial neighbour must carry its real distance
and its own observation date. Silently substituting a neighbour for an exact answer is the same bug
class as a lane reporting success having written nothing.

Agent row reads share the map's availability-authorized listing. A `LaneWindow` retains the exact
receipt-bound evidence source used to classify it, so a later absence decode cannot fall back to a
physical listing or cross into a newer generation. Release tools freeze that authorized view before
their two-pass row plan/replay. A bare receipt key never reaches an agent DuckDB statement: the
selected completion and part digests are verified on the serving worker, and every statement in that
tool call reads the same request-scoped local files containing those verified bytes. Authority
loading, evidence GETs, temporary writes, schema probes, and row scans all stay off the Sanic event
loop. Static lookup behavior is unchanged. This path is read-only: agent
queries do not repair receipts, publish availability, or enqueue ingestion.
