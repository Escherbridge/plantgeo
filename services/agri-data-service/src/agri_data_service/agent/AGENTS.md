# The location-analysis agent graph

## Strategy knowledge (literature) tools (2026-09-26)

`search_environmental_strategies`, `get_environmental_strategies` and
`search_strategy_research_findings` are registered in `tools.py::WAREHOUSE_TOOLS`; their client,
refusals and projection live in `strategy_knowledge.py`, which imports nothing from `tools.py`. Each
calls `POST {STRATEGY_KNOWLEDGE_URL}/v1/tools/{search_strategies|get_strategy|search_findings}` on the
`plantgeo-strategy-knowledge` service. Being in the one registry, they reach every agent surface: the
graph's warehouse and web passes, the Next.js tool bridge (`routes/agent_tools.py`) and MCP.

**Why a separate service, over private networking.** The knowledge base is a Chroma index plus an
ONNX embedder with its own corpus lifecycle (a bucket-published `corpus_version`, and a
`--pull-from-bucket` start that fails the deploy rather than serve half a corpus). Carrying that here
would add heavy dependencies to a service whose toolchain avoids unrelated `uv sync`s, and couple two
release cadences. The owner decision (2026-09-26) keeps it private-network only, with no public
domain. `config.py` therefore accepts `https` anywhere but plaintext `http` only on loopback or a
`*.railway.internal` host, and never a credential, path, query or fragment.

**Coordinate-free by design.** The service never sees a coordinate. Since wave 2 (C4 relaxation,
owner decision 2026-09-27) it may see the one `Region` enum value the server derives FROM the
coordinate -- a coarse label, never the point -- plus the user's own words as `context_query`; see
"Server-owned site facts" below. Field names mirror strategy-knowledge `site_profile.py`: soil,
slope and burn values boost, while `land_cover` and `region` filter. Filters are `Literal` enums
copied from strategy-knowledge `vocabulary.py`, so the schema publishes every accepted value.
`test_agent_strategy_knowledge.py` parses the service's own source and fails on drift. An invented
value fails validation locally and never reaches the service. `search_environmental_strategies` and
`search_strategy_research_findings` also take a top-level `region: RegionFilter` (distinct from
`site_profile.region`), forwarded to the service's own `region` filter on the external path.

**A lone string is a one-element list filter.** Every list filter -- `goals`, `land_use`, `region`,
`fire_phase` and `get_environmental_strategies`' `strategy_ids` -- carries the same
`BeforeValidator(strategy_knowledge._wrap_lone_string)`. A live eval (gemini-2.5-flash-lite) sent
`region="pnw_westside"` on every call; Gemini ignores docstrings, so a prompt could not fix the argument
shape (STRATEGIES.md "Do NOT do"). The wrap runs BEFORE `list[...]` validation, so an invalid lone value
fails exactly as it would inside a list, and a BeforeValidator adds nothing to the published schema.
It also covers the live map agent, which validates through the bridge.

**Portable schema.** Pydantic would publish the nested `site_profile` as `$defs`/`$ref`, and each
optional parameter as `anyOf [X, null]`. No other bridge tool publishes either, and the live map
agent forwards the catalogue to Gemini through OpenRouter. `strategy_knowledge.literature_tool` is
therefore `beta_async_tool` with an explicit `input_schema`: the inferred schema with references
inlined and null branches collapsed, and `_patch_site_profile_schema` substitutes the strict
`StrategySiteProfile` shape back in over `site_profile`'s own loosened type (below). Validation still
runs against the function signature. For the same reason `burn_severity` and `land_cover` are
published as strings, coercing numbers; the service's `str | int` fields parse a digit string as an
MTBS class or NLCD code.

**`site_profile` is advisory, not a filter.** (External path only: with a server context bound the
model's `site_profile` is discarded whole; see "Server-owned site facts".) It only boosts or filters the ranking, so one bad hint
must not sink an otherwise-good call: `search_environmental_strategies` and
`search_strategy_research_findings` type it as a loose dict and `strategy_knowledge.sanitize_site_profile`
validates each key on its own against `StrategySiteProfile`, dropping an unknown key or an
out-of-range value into a bounded `site_profile_ignored` list on the payload instead of rejecting the
whole call, while every FILTER (`goals`, `land_use`, `fire_phase`, `min_evidence`, `strategy_id`,
`ids`, `limit`, `query`) still validates strictly against the function signature and rejects outright.
A live Gemini eval also echoed its own Python-style call site, `default_api.search_environmental_strategies`,
instead of the published tool name; `llm.py::_canonical_tool_name` strips exactly that prefix when the
remainder names a registered tool, so the call still dispatches.

**Model-facing descriptions.** `agent ask` and MCP clients see no system prompt, so the three
docstrings carry the calling rules themselves. First, the tools need no date, coordinate or layer,
so a "how can we" question calls them directly; a live Gemini eval asked the user for dates instead.
Second, `site_profile` holds only the listed measured keys, never the selected day, coordinates,
range or surface name; the same eval stuffed those in. Third, the literature attribution rule.

**Bounds.** An 8 s wall-clock deadline (`asyncio.timeout` around the httpx call) sits under the
bridge's 12 s deadline. The response is capped at 1 MiB, checked against the declared length and
counted while streaming. The request sends `Accept-Encoding: identity`, so the count is wire bytes and
nothing is inflated before the check. A response that declares any other `Content-Encoding` is
refused unread (`strategy_knowledge_unavailable`). `config.py` also rejects control characters and
whitespace anywhere in `STRATEGY_KNOWLEDGE_URL`: `urlsplit` silently drops tab/CR/LF, so a split
host would validate and httpx would raise `InvalidURL` at call time. Other bounds: at most 10
results, 1-5 ids and a 500-character query. The
payload is a bounded projection: identity, summaries, family, goals, evidence strength, actions,
rates as stated, citation title/URL, finding magnitudes and excerpts, `claim_tier` and
`corpus_version`. Facet texts, snippets, scores and paging internals are dropped.

**Observability and concurrency.** Every POST carries `X-Request-ID` (a fresh `uuid4().hex`) beside
`Accept-Encoding: identity`, and every `ask` emits one `literature_call` structlog event
`{tool, status, ms, result_count, request_id}`: `status` is `answered` or the refusal code,
`result_count` is null on a refusal, `request_id` null when nothing was sent (not configured). The
strategy-knowledge side logs the same id, so one agent call is traceable across both services. The
event is safe on the stdio surfaces: an unconfigured structlog prints through the `sys.stdout` that
`mcp_server.reserved_stdout` has already pointed at stderr. `ask` holds a `MAX_CONCURRENT_CALLS` (4)
Semaphore around the exchange, created lazily PER EVENT LOOP (`_call_slot`, keyed by the loop and
pruned of closed loops): an `asyncio.Semaphore` binds to the loop it first waits on, so one shared
module-level Semaphore would raise under contention in the eval harness, which runs `asyncio.run` per
scenario. Above its own uvicorn `limit_concurrency` strategy-knowledge answers a plain 503, which
`_exchange` maps like any other non-200 to `strategy_knowledge_unavailable`.

**Refusal states.** Each is a payload, never an exception, whose note opens "This is a REFUSAL, not
an absence". A refusal is a fact about the service, never about the literature.

| Code | When |
|---|---|
| `strategy_knowledge_not_configured` | `STRATEGY_KNOWLEDGE_URL` unset; no request is sent |
| `strategy_knowledge_unavailable` | timeout, connection error, 5xx or `not_ready`, any other non-200, over-budget or non-JSON body |
| `strategy_knowledge_rejected_arguments` | 400 (or 413) from the service; its error detail is echoed |

**Why metadata-exempt.** Literature is not a measurement at this location. Every call records
`row_count=0` with `evidence_domain: literature_reference`, and all three names sit in
`graph.py::LITERATURE_TOOLS`, which is part of `_METADATA_TOOLS`. Exemption is by name, so even a
mis-recorded row count cannot make the sufficiency gate treat literature as local coverage or use it
to close the web pass. For the same reason the gate's `coverage.tools_available` count excludes them.

**Provenance rule.** A claim grounded in these tools carries `evidenceOrigin: "literature"` and
`evidenceSource: "strategy-knowledge"` (`report.py`, mirrored in `src/lib/regional-intelligence.ts`;
the drift test reads both). `report.py` enforces the pairing in both directions on `Observation` and
`RemediationRecommendation`: literature requires strategy-knowledge, and strategy-knowledge is
allowed only on literature. `RiskSummary` may be neither literature-origin nor list
`strategy-knowledge` in `evidenceSources`, matching `remediation-report.ts`, where the risk judgement
is never literature. `evidenceReadIds` stay warehouse-only; the existing validator rejects them on a
literature claim. Magnitudes and rates are quoted only as reported, with their conditions, and never
extrapolated to the selected site. Literature is not time-bound, so no freshness max-age applies.

**Literature/strategy-knowledge normalisation.** `messages.parse` gives the graph no correction
round, so a single mislabelled claim used to fail the whole parse. `Observation` and
`RemediationRecommendation` each carry a `model_validator(mode="before")` running
`_pair_literature_provenance` ahead of the strict pairing check: a `literature` claim with a missing
or empty `evidenceSource` gets `"strategy-knowledge"` filled in, and a `web`/`model_inference` claim
that cites `"strategy-knowledge"` has that source stripped. It never changes an `evidenceOrigin` or
touches `evidenceReadIds`, and a genuinely conflicting source (e.g. `literature` + `"soil-phh2o"`)
still reaches `_require_literature_pairing` and is rejected. This mirrors `pairLiteratureProvenance`
in `remediation-report.ts`, which runs the same two-way fill/strip ahead of Zod validation.

**A literature claim needs a literature answer from this run.** `/agent/analyze` checks this after
`synthesize_report` parses the report (`graph.py::literature_answered`). The check needs one ledger
entry for a `LITERATURE_TOOLS` tool with `state: "answered"` AND `result_count > 0`, from either pass
-- a reachable service that matched nothing still teaches the model that, but backs no claim, so a
zero-record answer does not unlock provenance. `result_count` already rides every answered ledger
entry (`strategy_knowledge.py::ask`): the searches count their `results` list, `get_environmental_strategies`
counts found `strategies`. The web pass's ledger is kept for this. If no qualifying entry exists,
every literature claim is downgraded, not rejected
(`report.py::downgrade_literature_claims`). It becomes `model_inference` with no `evidenceSource`,
and a `progress` event with status `literature_downgraded` lists the claim paths. Why downgrade:

- With no answered call, the claim can only come from the model's own knowledge, and
  `model_inference` is the honest label for that.
- A downgrade only ever weakens provenance.
- The graph has no correction round, unlike the TypeScript flow, so a rejection would throw away
  the whole report, warehouse observations included, over one labelling mistake.

### Server-owned site facts (wave 2, seams S1/S2)

Owner decision 2026-09-27: site facts are SERVER-OWNED. Evals caught models inventing them (Gemini's
"slope (50%)" with no slope read) and dropping the user's own words ("sour pasture" became "soil
chemistry"), and more prompt text is the wrong fix. So the server, out of band, binds a
`strategy_knowledge.StrategyContext` -- `user_question`, the map point, optional `SiteFacts` -- in a
ContextVar (`bound_strategy_context` / `current_strategy_context`), and `plan_literature_call` decides
what each call sends:

| | server context bound | no context (MCP, `agent ask` without the harness, external) |
|---|---|---|
| `site_profile` sent | `SiteFacts` + `region=derive_region(point)` when not None | the model's, sanitized field by field (advisory, below) |
| model `site_profile` / top-level `region` | DISCARDED, named in `site_profile_dropped` (`region(argument)` for the filter) | kept |
| `context_query` | the verbatim `user_question`, searches only, when non-empty; else the brief's `site_brief_query` | never |
| `context_query_source` | `"user_question"`, `"site_brief"` or `"none"` (searches only) | omitted |
| `site_profile_provenance` | `{key: {basis, label}}` for every forwarded key, `region` included | omitted |
| `site_profile_source` | `"server"` | `"caller_asserted"`, or `"none"` when no site_profile was sent |

`get_environmental_strategies` takes no profile and never forwards `context_query`, but carries the
same labels, so every literature payload in a context-bound run says who owned the site facts. The
model-facing schemas are unchanged: `context_query`, `server_context` and `point` are never
parameters, so a model can neither see nor set them. Bound by:

- the graph: `AgentRequest.strategy_context()` for both model passes, via `tools.run_context(strategy_context=...)`;
- the eval harness (`scripts/agent_strategy_eval.py`, seam S5): a fresh `run_context` per user turn;
- the bridge: `AgentToolCallRequest.server_context` (the S1 wire shape `ServerContext`, `extra="forbid"`),
  bound per call with `bound_strategy_context` for the three literature tools ONLY; any other tool
  ignores it. A malformed `server_context` alone is a 400 `invalid_tool_arguments` whose detail names
  each field (`server_context.point.longitude: ...`) and never echoes a value.

`user_question` is the user's messages this conversation, joined with newlines, latest last; the model
strips control characters (newlines kept) and keeps the LAST 2000 characters (strategy-knowledge's
`context_query` limit), so the newest turn survives truncation. `SiteFacts` is `SiteProfile` minus
`slope_pct` (no slope surface exists) and `region` (derived); a drift test reads the service source.

**What the graph supplies: the site brief's facts (soil data plane, CONTRACT C3/C4).** The earlier
`site_facts=None` rule stood because no reader returned a trustworthy, unit-checked number and parsing
tool payloads back would reinvent facts. The `build_site_brief` node now reads them server-side, into
integers, before the model starts (see "Site brief" below), and `graph.py::brief_literature_context`
fills `SiteFacts` from the brief exactly as C3 maps it: `soil_ph`, `soil_organic_carbon_pct`,
`sand_pct`, `clay_pct` from the SoilGrids 0-30 cm topsoil; `days_since_fire` and `burn_severity` from
the fire section; `land_cover` = the CDL class name. `AgentRequest.strategy_context()` itself still
returns the question and point only; `GraphContext.strategy_context()` prefers the brief's context.

`site_facts` means "server-read values, each with provenance", no longer "measured only". Each key
carries a `FactProvenance` (`basis` in `measured | model_estimate | classified |
classified_remote_sensing | survey_estimate`, `source`, `label` <= 200, optional `release_id`,
`depth`, `resolution_m`, `distance_m` <= 2,000 for a model estimate). **Provenance-aware drop
(critic #11):** a caller that sends `site_facts_provenance` loses every `site_facts` key without an
entry, named `<key>(no_provenance)` in `site_profile_dropped`; a legacy caller (no provenance at all)
keeps wave-2 behaviour and each forwarded key is echoed with basis `provenance_absent_legacy`. Legacy
web never sends soil values, so no soil number can ride the legacy path. `site_brief_query` (<= 600
characters, every control character stripped) is the brief's literature seed and becomes
`context_query` only when the user typed nothing: `user_question` stays the user's own words, never
brief text. The strategy-knowledge `SiteProfile` still receives bare values; the label rides beside
each value in the payload's `site_profile_provenance`, and the prompt tells the model to repeat it.

**S3 echoes pass through verbatim.** `query_intent` and `context_query_used` are forwarded unchanged
(`strategy_knowledge.py::_PASSTHROUGH_ECHO_KEYS`, outside the bounded projection) whenever the service
sends them; a live eval found the projection had been dropping them, which hid how the service read the
query.

### The region table

`strategy_knowledge.derive_region` walks `REGION_BOXES` -- inclusive lon/lat boxes, FIRST MATCH WINS --
and returns that box's region, or None. Why a hand-written table and not a polygon dataset: 18 coarse
labels, no new dependency or data file, and a table a reviewer can check line by line. Why it errs to
None: `site_profile.region` becomes a HARD region filter in strategy-knowledge (it admits the region
plus `general`, untagged, `global` and, for North America, `north_america_general`), so a wrong label
hides relevant records, while None merely skips the filter. Hence:

- Ambiguity bands come first and return None: a +-0.1 degree band on the Cascade crest, the Wyoming
  basins, eastern Utah, Rio Grande and St. Lawrence/Niagara/Rainy River border strips, the Ohio valley
  and Mid-Atlantic transitions, the Bering Strait, Greenland, the Levant/Arabia, New Guinea.
- pnw_westside/pnw_inland split at the Cascade crest: -121.3 in Washington (Snoqualmie -121.4,
  Stevens -121.1), -121.7 in northern Oregon (Hood, Jefferson), -122.1 in southern Oregon (Crater
  Lake) -- a single -121.3 line would put Sisters and Government Camp on the wrong side.
- Southern Idaho's Snake River Plain and Owyhee (the Boise eval scenarios) are
  `great_basin_high_desert`: sagebrush steppe, where the corpus tags post-fire mulching and the
  cheatgrass/sagebrush strategies; the Palouse and the Idaho panhandle are `pnw_inland`.
- Never `north_america_general`, `global` or `general`: the filter admits those itself.
- Open ocean, Hawaii, the Pacific islands and the Ural gap match nothing and return None. Boxes do
  include adjacent coastal water, and a few border cities on a river boundary can land on the wrong
  side (Windsor, Ontario reads us_midwest); southern Vancouver Island reads pnw_westside on purpose.

This is the C4 relaxation: strategy-knowledge still never receives a coordinate, only this enum.

**Open item (not implemented): approved-only literature.** The tools pass on every record the service
returns, whatever its `review_state` (`machine_extracted` included). They label that state but do not
filter on it. An approved-only filter, like the one `species_information` applies to companion
evidence, is still owed. It would go either in the service or in the `strategy_knowledge.py`
projection.

## Loop hardening (`agent/llm.py::OpenAiCompletionsClient.converse`, wave 2 seam S5)

**Identical-rejected-call guard.** A tool call whose canonical name plus canonical-JSON arguments
(sorted keys, no incidental whitespace, `_canonical_call_key`) equal an earlier call THIS conversation
that came back rejected is answered with `{"error": "repeated_rejected_call", "detail": "<prior
detail>; change the arguments"}` instead of being executed again, and the ledger entry carries
`"skipped": "repeated_rejected_call"` -- a model spinning on the same bad arguments cannot keep
draining the iteration budget on it. "Rejected" excludes `strategy_knowledge_not_configured` and
`strategy_knowledge_unavailable` (`_UNGUARDED_REJECTION_ERRORS`): those name a fault in the SERVICE
(unreachable, unconfigured), not the call's own arguments, so an identical retry can genuinely answer
differently the second time and must never be suppressed. A schema-shape rejection and every
deterministic warehouse four-state refusal stay guarded, since both are a pure function of their
arguments -- **except** `parquet_serving_refused`'s own `serving_at_capacity` and
`release_read_changed` refusal codes (`_TRANSIENT_SERVING_REFUSAL_CODES`), which are a fact about the
serving PROCESS at that instant, not the call's day/lane/viewport arguments; every `parquet_serving_
refused` payload shares that one top-level `error` string regardless of which fault produced it, so
the guard reads the nested `refusal_code` rather than exempting the outer code wholesale (wave-2
fix-stage review: a blanket exemption would also un-guard `read_over_budget`/`day_conflict`/etc.,
which `parquet_ops/faults.py` itself documents as NOT retryable).

**Final no-tools answer turn.** When `max_iterations` chat turns have all come back asking for more
tools, `converse` does not raise and does not give up with an empty answer. It appends one short user
nudge and makes ONE further `chat` call that still publishes the tool schemas but forces
`tool_choice="none"`, rather than omitting `tools` outright: the transcript by then already holds
`tool_use`/`tool_result` blocks, and both Anthropic and OpenRouter reject a request carrying those with
no `tools` defined at all. A provider that ignores `tool_choice="none"` and answers with `tool_calls`
anyway has them stripped from the appended message, so a later turn's request never carries a dangling
assistant tool-call with no reply; `stopped_because` is `"iteration_budget_exhausted_answered"` only
when that call actually returned text, else `"iteration_budget_exhausted"` -- the same label the
graceful-failure path below uses. If even that call is rejected, `converse` falls back to the old
graceful outcome (empty `final_text`, `stopped_because="iteration_budget_exhausted"`) rather than
letting `LlmProviderError` escape and discard every tool result already gathered.

**`agent_system_message(today) -> dict`.** Returns `{"role": "system", "content": "Today is <iso
date>. " + INSTRUCTIONS}`. `INSTRUCTIONS` lives in `llm.py` (`mcp_server.py` imports and re-exports it
under the same name) so this function needs no `agent.llm` <-> `agent.mcp_server` import cycle --
`mcp_server.py` already imports several names from `llm.py`, so the dependency can only run one way.
`converse` does NOT add this message implicitly; `interface/cli/agent.py::_ask` and
`scripts/agent_strategy_eval.py`'s eval harness both call it explicitly, so the two never carry
independently drifting copies of the system prompt.

## Selection evidence and RAG contract (2026-09-20)

`list_environmental_layers` discovers every map surface, including community and soil-raster
variants. `surface_evidence_for_selection` is the numeric retrieval
entry point shared by MCP, the HTTP tool bridge and both agent workflows. Older radius tools
remain internal compatibility helpers where needed; they are not model-facing choices, except
`drought_history_at_point` and `fire_history_near_point`, re-published 2026-10-04 (see
"Closest-datapoint reads (2026-10-04)").

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
one to three lanes, or two days for four lanes. The shared page budget admits at most
sixteen lane-day resolutions including selected-day and substitution reads; cold multi-depth requests must fit
the ordinary tool deadline. The history schedule starts with the selected day and its nearest
actual published neighbours on both sides, then both active-window endpoints, and never schedules
a day after the server's UTC today (see "Closest-datapoint reads (2026-10-04)"). Subsequent pages distribute
reads across each lane's published dates before visiting unwritten calendar gaps. The plan uses
the same authorized inventory as subsequent reads; integer `page_start` offsets eventually cover
every requested day without omissions. The response names its effective `days_per_page`, sampled
days, full requested count, next offset and completeness. The catalogue reports the maximum budget.
An incomplete page supports a sampled comparison, never a complete trend or absence on unread
days. Up to 36,600 calendar days may be requested without silently narrowing to newest data.

App-owned community and raster surfaces use the bounded `/api/v1/map-evidence` proxy only when
`AGENT_MAP_APP_URL` is configured. Missing configuration is a typed refusal.

Regional climate and soil gates bind their concrete surface names to NASA POWER and
ERA5-Land respectively, so availability never depends on a retired generic layer binding.

The older sections below record previous contracts and migration evidence. This selection
contract and the executable registry govern current model-facing retrieval.

## Closest-datapoint reads (2026-10-04)

Owner decisions 2026-10-04, after a production diagnosis found the agent answering "no data" where
the map showed data: page 1 never reached the nearest published days, an exact-day miss had no
fallback, the spatial search stopped at the one map tile, and soil survey was read through the
observed-partition listing that answers `day_not_written` at every date. All changes are additive:
every earlier state and field keeps its meaning; the ones below are new.

**1. Nearest published day, per-lane tolerance** (`day_tolerance.py`). One named rule, the
*two-interval rule with a three-day floor*, derived from the lane registry:

| lane registration | mode | tolerance |
|---|---|---|
| `static_lookup` (no time axis) | `static` | n/a: read at the current release |
| `release_series`, `cadence_days == 1` (MTBS, crop-cover, forecasts) | `as_of` | n/a: the existing as-of rule |
| `release_series`, `cadence_days > 1` (weekly drought) | `nearest` | 2 x cadence = 14 |
| `daily_series` in `SPARSE_REVISIT_LANES` (vegetation NDVI) | `nearest` | 2 x `publication_lag_days` = 14 |
| every other `daily_series` | `nearest` | 3 |

**Near today the bound absorbs the lane's settle lag** (`DayTolerance.for_request`, added after
the first production run on 2026-10-04): a lane cannot have published anything newer than
`today - publication_lag_days`, so the tolerance gains `max(0, lag - (today - requested))` days.
ERA5 soil and climate lanes settle 5 days behind, so "today" may borrow up to 3 + 5 = 8 days back;
a request older than the lag keeps the plain bound. A sparse-revisit lane gets no allowance, since
its lag field is its revisit gap and already sits inside its interval. A request after today is
treated as today (the stretch never exceeds the lag). The lane's wire `tolerance_days` is the bound
actually applied, with `settle_lag_days` beside it; the catalogue's `lane_day_tolerance` lists each
lane's BASE bound and its `settle_lag_days`, not a request's stretched bound.

The NDVI row is the one hand-spelled fact: a `daily_series` may not declare `cadence_days > 1`,
so the registry records the measured 7-day revisit gap as its `publication_lag_days`, and
`SPARSE_REVISIT_LANES` says "read that as the interval". When the selected day is `day_not_written`
and a published day lies within tolerance, the selected entry becomes `published_nearest`, served
from that day; a tie goes to the EARLIER day (it is settled). Beyond tolerance the entry keeps its
unpublished state and adds `nearest_published_day`/`nearest_day_offset` ("nearest is 40 days back").
**A `governed_absence` is never substituted** (review fix H3, 2026-10-04): it is a published answer
-- for fire-detections, FIRMS returned zero detections (`fire_detections/adapter.py`) -- so another
day's numbers must not replace a measured zero. It keeps its state and gains
`nearest_published_day`/`nearest_day_offset` as information only, inside tolerance or not. A
`conflict`/`incomplete` day still refuses: a warehouse fault is not a gap to paper over. Substitution applies to the SELECTED day only; history entries stay exact
calendar samples, so history never borrows. The nearest search reads the listing for the window's
years plus one year each side of the selected day, and never a day after the server's UTC today
for an `observed` read. `selection_scope.schedule_ceiling(kind, today)` is the one rule: `observed`
stops at UTC today (an observation dated tomorrow does not exist), `forecast` has no ceiling (its
future days ARE the data), for the history schedule and the nearest-day search alike
(`lane_selection(kind=...)`). No surface routes a forecast-kind read through this reader yet:
`fire-risk` and `weather-forecast` are `APP_SURFACE_NAMES`, answered by the map app's reader.
The system prompt is deliberately unchanged (`test_both_flags_off_is_the_wave_two_graph_exactly`
pins it byte for byte): how to word a `published_nearest` or `nearest_cell` answer travels in the
tool result's own `note`, beside the evidence it explains.

**2. Always the nearest cell** (`selection_reads.SelectionReader`, `selection_geodesy.py`). When no
support in the tile covers the point, an expanding-box nearest-neighbour search runs the SAME
selection SQL over probe-centred boxes (0.25 degrees, then x4 each step, at most six steps), stopping
once the best hit lies inside the box's inscribed circle (nothing outside the box can be nearer) or
the box covers the region envelope. Each step re-reads only the receipt-verified local copies of
that day's parts. The search runs ONLY for the selected-day answer (and its substitution read;
`SelectionReader.nearest_search`): history days stay tile-only samples, so a page never multiplies
the search. **Station lanes** (point support with lattice size 0: water-gauges and
weather-observations at the base rung) never "cover" a point, so their nearest station IS the
answer: one bounded query over the region envelope widened to hold the probe, ordered by distance
and capped at `NEAREST_CELL_CANDIDATES` rows (`_nearest_station`), never the six-box search.
**Sparse-area lanes** (`selection_reads.SPARSE_AREA_LANES`: drought, fire-perimeters,
evacuation-zones, land-context-boundaries, burn-severity) report a nearest polygon as
`spatial_relation: "nearest_area_outside"`: the point lies inside NO area, which is itself the
answer ("not inside any drought area"), and the nearest area and its centroid distance are context,
never the value at the point. The rule is the lane's MEANING, not its storage: `GeometrySupport`
also backs TILING polygon lanes -- crop-cover's wall-to-wall equal-area grid, watersheds' HUC units
-- whose nearest polygon is a real value, reported as `nearest_cell` exactly like a lattice cell
(a regression fixed 2026-10-04 after the first cut keyed on `geometry_centroid`). Nothing in the
lane registry or schemas separates the two, so the set is curated; the web mirrors it as
`REGIONAL_SPARSE_AREA_NOUNS` and `test_the_web_sparse_area_surfaces_mirror_the_agri_lanes` pins them
equal. `distance_km` is a great-circle (haversine) distance and `distance_km_basis` says
to what: `cell_edge` (lattice support box), `source_coordinate` (point lanes with no lattice),
`geometry_centroid` (polygon lanes; the exact covers test is `ST_Intersects`), `delineation_edge`
(SSURGO, local equirectangular, sub-percent at the <= 9 km search bound), or `covers` (0 km).
SSURGO's nearest delineation also carries `proven_nearest`: `false` when the best hit of the last
search box lies outside that box's inscribed circle (a nearer delineation beyond the box cannot be
ruled out). The hit is returned rather than discarded; `nearest_search` says why the search stopped.

**3. History ranking.** Page 1: selected day, nearest published before, nearest published after,
then the window endpoints, then published dates, then calendar gaps. `MAX_LANE_DAY_READS` is 16:
every dated lane reserves `RESERVED_READS_PER_LANE = 2` reads outside its history page (the
selected day and the one nearest-day substitution read), so four-lane soil temperature reads two
history days and three-lane surfaces read three, and `lanes x (page_days + 2) <= 16` holds for every
catalogue surface (pinned against `SURFACE_PARQUET_LANES`). The substitution read reuses a history
read of the same day when that read already covers the point. Each lane reports `lane_day_reads`
and the envelope's `history.lane_day_reads` sums them (also logged). Peak memory does not grow with it: each lane-day is a separate capped query run in
sequence inside one `SERVING_MEMORY_LIMIT` session, so `read_over_budget` is per query. The real
cost is latency against the 12 s `TOOL_TIMEOUT_SECONDS`, which is the first thing to watch in the
`agent_tool_call` log (`duration_ms`). A test pins the two-day floor against `SURFACE_PARQUET_LANES`.

**4. Static lanes.** `nature_has_time_axis` false: read ONCE at the current release, no date, no
history, `static: true` on the lane and its selected entry, `release_day` instead of `served_day`.
`soil-survey` reads through `planes.soil_survey` (`soil_survey_reads.py`), the admitted-release
reader `interface/http/soil_survey.py` serves the map from, returning map-unit properties without
geometry. Other static lanes use the map's `/release` rule, `resolve_release(as_of=UTC today)`.

**5. One structured log event per bridge call.** `routes/agent_tools.py::call_agent_tool` emits
`agent_tool_call` (fields built in `tool_call_log.py`): `tool`, `surface`, `lanes`,
`requested_day`, `served_day` (when all lanes agree), `day_offset` (furthest from zero across
lanes), `spatial_relation` (`nearest_cell` if any lane fell back), `distance_km` (largest),
`state` (lanes' shared state, else `mixed`; `rejected`/`error` for 400/503 paths), `lane_states`,
`static`, `refusal_code`, `duration_ms`, `record_count` (ledger rows), `lane_day_reads` (every
resolution the call made, substitution reads included). Never arguments, coordinates
or payloads.

**6. Drought and fire history re-published.** `drought_history_at_point` and
`fire_history_near_point` take only numbers and an optional ISO day: no enum array, so they add
nothing to the Gemini forced-call "too many states" count (an array of enums with a bound is the
known trigger). `test_agent_closest_datapoint.py` checks that and drives the drought tool through
the HTTP bridge over real DuckDB. The whole round-1 budget is measured on the web against the
CURRENT catalogue (soil flag on, as production runs): `src/__tests__/services/
agri-tool-catalogue-current.fixture.json`, which
`test_the_web_current_catalogue_fixture_is_the_published_catalogue` keeps equal to
`environmental_tool_schemas()`. Measured 2026-10-04: 100 properties / 84 enum values / 30
constraints with the web's own tools, against the last accepted 112 / 208 / 102 (rejected at 117
properties). Regenerate the fixture whenever a tool schema changes.

### Contract for the web (`regional-analysis-evidence.ts` evidence fields)

Read from `result.lanes[i].selected` of `surface_evidence_for_selection`:

| wire field | values | web evidence field |
|---|---|---|
| `state` | adds `published_nearest` beside `published`, `governed_absence`, `day_not_written`, `lane_never_written`, `refused` | `status: "observed"` for both `published` and `published_nearest` (add it to `RECORD_STATES`) |
| `requested_day` | the caller's selected day (absent on static lanes) | `selectedDate` |
| `served_day` | the day actually read (`release_day` on static lanes) | `resolvedDay` |
| `day_offset` | `served_day - requested_day` in days, signed; 0 = exact | `dayOffset` |
| `nearest_published_day`, `nearest_day_offset` | on a `day_not_written` entry beyond tolerance, and on EVERY `governed_absence` | the lane's gap line ("no record on 2026-10-04; nearest published 2026-08-25 (40 d earlier, beyond the 3-day tolerance)") |
| `tolerance_days`, `tolerance_rule`, `resolution` | on each lane entry | `tolerance_days` words the gap line; the rest diagnostic |
| `spatial_relation` | `covers`, `nearest_cell` (grid cell, station or SSURGO delineation: used as the answer), or `nearest_area_outside` (sparse-area lanes only, `SPARSE_AREA_LANES`: inside no area; information only) | `cellDistanceKm` for `nearest_cell` only; `nearest_area_outside` becomes the gap line "not inside any drought area; nearest N km (to its centroid)" |
| `distance_km`, `distance_km_basis` | great-circle km, and what it measured (`cell_edge`, `source_coordinate`, `geometry_centroid`, `delineation_edge`, `covers`) | `cellDistanceKm`; the basis words the gap line (cell / station / delineation / area centroid) |
| `proven_nearest` | SSURGO only: `false` when a nearer delineation beyond the searched box is possible | (diagnostic) |
| `static` | `true` on static lanes (and their selected entry) | `staticLayer` |

The web reads these from the SELECTED entry first (soil survey's features carry none of them) and
falls back to feature-level fields for older readers. Feature-level `spatial_relation` keeps
`contains_selection`/`intersects_selection_tile` and adds `nearest_cell`/`nearest_area_outside`;
each feature also carries `distance_km`/`distance_km_basis`.

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
the drought and fire history summaries (re-published 2026-10-04), the caller-scoped species lookup,
and the three strategy-knowledge literature tools (herbaria evidence was retired 2026-10-03; see
"Herbaria surfaces are retired").

## Topology

```
build_site_brief ─▶ gather_warehouse_evidence ──▶ assess_sufficiency ──┬─(insufficient)─▶ web_evidence ─▶ synthesize_report
                                                                       └─(sufficient)──────────────────▶ synthesize_report
```

Five nodes, declared as frozen dataclasses with typed outputs, walked by `execute_graph()`.
`build_site_brief` is pure Python (no model call); see "Site brief". With `SITE_BRIEF_ENABLED` off it
returns at once, emitting nothing, so the walk is the wave-2 four-node graph.
The edges are in `GRAPH_EDGES` as data so the topology can be asserted rather than inferred,
and `test_agent_graph.py` does assert it.

The shape is the point. Two of the four nodes are model-driven; the edge between them is
not. `assess_sufficiency` is ordinary Python operating on a ledger of what the warehouse
tools actually returned, so **whether a request is allowed to touch the public web is
decided by the service, not by the model**. The TypeScript version left that to the model,
bounded only by a `MAX_SEARCHES_PER_REQUEST` counter enforced when the tool was called; here
the web-search tool is not even present in the request until the gate opens it.

The budget rule mirrors `ai-prompt.ts`'s intent, expressed as coverage rather than rounds:

`populated_sources` counts measured layers only: catalogue and coverage metadata, the literature
tools and model-estimate tools (`_MODEL_ESTIMATE_TOOLS`: `soil_properties_at_point`) never count, so a
SoilGrids estimate can never close the web gate (review M3).

| Distinct measured surfaces that returned rows | Verdict | Search budget |
|---|---|---|
| 0 | insufficient | 3 (`MAX_SEARCHES_PER_REQUEST`) |
| 1 | insufficient | 2 |
| 2+ **and** the caller asked a specific question | external guidance allowed | 1 |
| 2+ otherwise | sufficient | 0 |

`gather_warehouse_evidence` seeds the transcript with the replayed history (last
`MAX_HISTORY_TURNS`, mirroring the TypeScript cap) and the volatile location context. Only
`synthesize_report` produces user-visible structure; every node before it produces evidence.

## Site brief (`agent/site_brief.py`, soil data plane CONTRACT C5)

A deterministic, bounded JSON (`site-brief/1`) built before the model starts, from reads the server
runs for this point: soil (SoilGrids lane), fire (MTBS + fire perimeters containing the point, and
satellite detections within 10 km over 30 days), USDM drought class, the nearest weather-station
reading (50 km, today else yesterday) and the USDA CDL dominant class of the crop-cover cell. Each
section is present with a basis and label, or `{"state": "unavailable", "reason": ...}` from the one
C5.6 vocabulary. The brief never invents a value; weather is context and seeds nothing.

- **Pure builder, twin in TypeScript.** `build_site_brief(inputs)` takes only normalised
  `SiteBriefInputs` whose soil values are ISRIC mapped **integers**; every derived number is integer
  arithmetic with round half up by integer division, `(2N + D) // (2D)`, emitted as `k / 10^p`, which
  has the same shortest round-trip form in Python and JavaScript. `canonical_json` (sorted keys, no
  spaces, integral floats as integers) is what parity compares; never raw bytes. The golden fixture
  `tests/fixtures/site_brief_golden.json` (12 cases, owned here, read by `src/__tests__/services/
  site-brief.test.ts`) pins both builders; case 1 is the CONTRACT's worked example. Change the builder
  and the fixture together, and tell the web lane.
- **Algorithms named**: thickness-weighted mean over 0-30 cm (weights 5/10/15), USDA soil texture
  triangle on normalised tenths (first match wins, C5.2 order), USDA reaction classes on pH tenths,
  SOC bands.
- **Flags (review M7).** `site_brief.py::flag_enabled` is the one parser for both switches: only the
  exact value `true`, whitespace trimmed, case-sensitive (the web parses them identically). With
  `SITE_BRIEF_ENABLED` and `SOIL_PROPERTIES_READS_ENABLED` both off the graph is wave 2's byte for
  byte: `build_site_brief` reads and emits nothing, no site facts, provenance or seed reach the
  literature tools, and `prompts.py::system_prompt()` sends `WAVE_TWO_SYSTEM_PROMPT`, the pre-soil
  text (its sha256 is pinned in `tests/test_agent_graph.py`). Either flag on sends the labelled
  `SYSTEM_PROMPT`.
- **Base run and follow-ups (review M6).** `AgentRequest.is_base_run()` is the first turn or an empty
  question. A follow-up's history holds only the saved turns, never the brief, so it is not left
  without soil: it gets the one soil read (`_follow_up`), rendered as a trailing "Soil estimate"
  section (`prompts.py::build_soil_estimate_section`) and seeded into the literature context as
  `model_estimate` site facts. It never repeats the other four section reads.
- **Fail open (review m4).** Building the brief (and its literature context) is wrapped: an unexpected
  input shape emits `skipped` / `brief_unbuildable` and the graph continues with no brief.
- **Readers live in `graph.py`**, not here, so this module stays pure and `tools.py` can import its
  soil-section formatter without a cycle. Each section runs under a 3 s timeout and fails soft to a
  reason; brief reads run in their own `run_context` and their ledger is discarded (server context,
  not model evidence, so they never move the sufficiency gate).
- **Slot cap (review M8).** `read_site_brief_inputs` puts one `asyncio.Semaphore(2)` in the
  `_BRIEF_READ_SLOTS` context variable; every serving-plane read a section makes goes through
  `_slotted`, so at most two brief reads hold slots on the 3-slot plane. The fire section's three reads
  (perimeters, MTBS, detections) run concurrently under that cap; the first failure in lane order
  names the section's reason.
- **Reader semantics shared with the web (CONTRACT C5.1; review M9).** The constants are pinned in
  `tests/fixtures/site_brief_reader_constants.json`, which `tests/test_agent_site_brief_reader_constants.py`
  and the web's `site-brief-readers.test.ts` both assert:
  - fire: the newest perimeter or MTBS burn containing the point, dated on or before today; the
    severity is the MTBS class (codes 2/3/4 or names, `site_brief.py::burn_severity_of`) of that same
    day's burn when exactly one is recorded, never a WFIGS `severity`; detections within 10 km over
    30 days, where no published day is 0 and a read that hits its row cap is `read_failed`;
  - weather: today's published day else yesterday's, nearest station within 50 km, ties to the newest
    reading; a reading missing temperature or humidity is `read_failed`;
  - land cover: integer class codes with a finite positive area only; a dominant class without a name
    is `read_failed`, never its numeric code;
  - soil: the coverage rule and radii in `soil_properties.py`, the same on the web.
- **Eval (DESIGN 8)**: `scripts/agent_strategy_eval.py --scenarios-file
  scripts/agent_site_brief_eval_scenarios.json` runs six base runs (`base_run: true`: no typed
  question, brief prepended, `user_question` None so the seed drives retrieval) plus the two
  follow-ups. Every turn records a `soil_labelling` report (unlabelled soil numbers, SoilGrids values
  called measured, `context_query_source` seen); it is reported beside, never inside, `score.passed`.
- **Why TS and Python both build it** (DESIGN 5.2): TS already reads fire, drought, weather and crop
  cover for its payload; rebuilding the brief in agri from the web's base run would re-read them on
  the 3-slot serving plane. The pure pair costs ~150 lines per language.

## Soil properties (SoilGrids) (`agent/soil_properties.py`, tool `soil_properties_at_point`)

- **What a value is**: an ISRIC SoilGrids v2.0 250 m machine-learning MODEL ESTIMATE, stored as the
  native pixel containing a 0.005-degree cell centre. It is never a measurement or a soil sample. Every
  soil value in the brief, the tool and the provenance carries the label "SoilGrids v2.0 250 m model
  estimate, <depth>", and the prompt tells the model to repeat it and never call it measured.
- **Kill switch**: `SOIL_PROPERTIES_READS_ENABLED` (only the exact value `true`, trimmed and
  case-sensitive, enables; `TRUE` no longer does) is read before any read. Off answers `reads_disabled`. This decouples a code push from exposing numbers
  (DESIGN 7, P5); `docs/env-vars.md` documents it for both services.
- **Read (CONTRACT C2)**: `warehouse.release_rows(layer="soil-properties", as_of=<server today>)` so
  a map slider day can never make soil vanish; `point_lane_rows` at radius + 400 m (it measures to
  the cell ORIGIN), then the nearest CENTRE by haversine on a 6,371,008.8 m sphere, ties to lower
  latitude then longitude, accepted within the SEARCH radius (below). Beyond it:
  `no_cell_within_radius` with the searched `radius_m`; the widening is never silent.
- **Nearest named estimate (owner 2026-09-28)**: SoilGrids masks urban pixels, so `search_radius`
  turns the DEFAULT radius (exactly 1,000 m after clamping to whole metres, rounded half up like JS
  `Math.round`; value equality, so a model that fills in the default is not strict) into a 2,000 m
  search; any other `radius_meters` (50-2000) stays strict. One read, then nearest selection; only the
  labels tell the cases apart. `site_brief.soil_estimate_is_nearest_fallback` classifies on the integer
  `distance_m` (> 1,000 m), and then every soil string names the distance with
  `soil_distance_phrase` ("nearest cell centre 1,340 m away (none within 1,000 m)"): the section
  label, the 0-30 cm topsoil label, both soil descriptors, the C3 provenance, and the tool's per-depth
  labels. The value is never presented as being at the point. The two soil descriptor seeds are
  empty, so a nearby cell's texture and SOC band never reach `literature_seed`/`site_brief_query` as
  this site's soil (the site facts still carry the values, labelled with their distance). Beyond
  2,000 m: `no_cell_within_radius`, `radius_m: 2000`. The tool reports the searched radius as `radius_m`
  on every result. Web mirror: `soilgrids.ts::soilSearchRadius`, `site-brief.ts::soilDistancePhrase`;
  pinned by `search_radius_cases` and `distance_phrase_cases` in the reader-constant fixture and the
  golden cases `urban_nearest_cell_beyond_default_radius` and `soil_boundary_1001_is_the_nearest_fallback`.
- **Integer contract**: every z13 value must be integral (|v - round(v)| < 1e-9) and non-negative, or
  the read is `read_failed` (a corrupt lane), never silently rounded.
- **Reasons**: `reads_disabled`, `outside_release_coverage` (no cell centre can lie within the radius
  of the pinned (-125, 42)-(-111, 49) lattice), `lane_never_written`, `not_published`,
  `no_cell_within_radius`, `serving_at_capacity`, `timeout` (3 s), `read_failed`.
- **Invalid coordinate (review m5)**: the tool answers in its own C6 shape, `state: "unavailable"`,
  `reason: "outside_release_coverage"` (the web reader's reason for the same input), `radius_m`,
  `soilgrids: null`, the note, plus the usual coordinate `error` text.
- **Why a dedicated tool, not a surface**: `agent/surfaces.py` is peer-owned and keeps `soil-*`
  refused in `surface_value_near_point`; the dedicated tool returns labelled physical values and never
  mapped units. Like the literature tools it publishes `strategy_knowledge.portable_schema` (no
  `anyOf`), because the bridge forwards its schema to Gemini. It performs no region-binding check: `soil-properties` is not bound in the region
  manifest by design (DESIGN 2.10), so `_region_absence` would refuse it.
- **Release lookback**: `serving.py::RELEASE_LOOKBACK_YEARS` is 12, so a 2020-06-02 release resolves
  until 2032; a later reader needs that bound widened or a republish.

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
uses `warehouse_tools_for_web()`, which omits `species_information`, instead of re-exposing a tool
whose UUID constraint has expired. If a later pass needs botanical access, it must re-enter the same
context with the caller's UUID and add a regression for mismatched and omitted UUIDs.

`warehouse_tools_for_web()` and the warehouse pass's own tool list are both built from
`tools.published_warehouse_tools()`, not from `WAREHOUSE_TOOLS` directly: `soil_properties_at_point`
is excluded from every published catalogue -- this one, `llm.tool_schemas`, the bridge's
`environmental_tool_schemas`, and `mcp_server.tool_descriptors` -- unless
`SOIL_PROPERTIES_READS_ENABLED` is exactly `true`, evaluated fresh per call. See
`agent/soil_properties.py`, "Gated by SOIL_PROPERTIES_READS_ENABLED", for the Gemini
`schema_too_complex` incident this gate exists to prevent. `WAREHOUSE_TOOLS` itself stays the full,
unconditional registry: `llm.tool_by_name` still resolves the soil tool by name with the flag off,
so the bridge `/call` route and the MCP/CLI tool-calling loop both still answer a direct call for it
with the tool's own `reads_disabled` refusal rather than an unknown-tool error.

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
`test_agent_strategy_knowledge.py::test_the_typescript_vocabulary_matches_the_python_projection`
asserts full equality: exact order for `EVIDENCE_ORIGINS`, and set equality for
`REGIONAL_EVIDENCE_SOURCES` and `REGIONAL_TOOL_EVIDENCE_SOURCES`. It found `groundwater` missing
here on 2026-09-26.
The camelCase field names carry a file-level `ruff: noqa: N815` for exactly this reason —
these are wire names, not Python identifiers we are free to restyle.

Report claim sources also accept the 39 map data surface names through
`RegionalClaimEvidenceSource`. The `literature` origin and `strategy-knowledge` source belong to the
strategy-knowledge tools; see "Strategy knowledge (literature) tools" above. The nine initial-context freshness keys remain a separate
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

### Herbaria surfaces are retired

Retired platform-wide on 2026-10-03 (owner directive "remove the herbaria dataset serving lanes and
so on generally", extending the 2026-09-28 PNW-only retirement): herbaria record where botanists
collected, not what grows where, so they are not the vegetation-type path (plant suitability and
LANDFIRE EVT are). Removed in every region, with no gate left behind:

- surfaces `botanical-occurrences`, `botanical-richness`, `botanical-collection-effort`,
  `gbif-occurrences` (from `AGENT_SURFACE_NAMES`, `SURFACE_REGION_LAYER_SLUGS` and the report's
  `RegionalToolEvidenceSource`, parity-pinned to `REGIONAL_TOOL_EVIDENCE_SOURCES` in
  `src/lib/regional-intelligence.ts`); a named one now answers `unknown_surface`;
- the `botanical_occurrence_current_release` tool and its siblings (`agent/botanical_occurrences.py`);
- the reader `planes/botanical_occurrences.py` and the `/api/v1/botanical-occurrences` routes;
- the producer `pipeline/direct/botanical_occurrences/` (GBIF / UBC / PNW Herbaria DwC-A
  quarantine, fetch and publish), `foundation/botanical_occurrences/`, the stream schema, and the
  `botanical_seed` sub-envelope plus the `botanical-occurrences` layer in both region manifests.
  The lane was never in `LANE_REGISTRATIONS`, a cron, the execution ledger or a migration, so no
  historic row names it and no retired-slug tolerance is needed on this side. The web keeps one:
  saved reports may cite the four slugs, so `RETIRED_REGIONAL_TOOL_EVIDENCE_SOURCES` still validates
  them in `REGIONAL_CLAIM_EVIDENCE_SOURCES` (`src/lib/server/services/AGENTS.md`).

`species_information` (and the `botanical_species_*` profile/reference planes) stay: they are the
canonical species reference, the plant-suitability direction, not herbarium evidence.

**Stored data is NOT deleted** (needs a separate owner go). Under the agri object-store root
(bucket + `OBJECT_STORE_PREFIX`), the whole `botanical-occurrences/` prefix: the pointer
`botanical-occurrences/availability/_LATEST.json`, any legacy `botanical-occurrences/current.json`,
and every generation `botanical-occurrences/<release_set_id>/` (`manifest.json`, `_COMPLETE`,
`releases/`, `raw/`, `identifications/`, `occurrences/`, `nonspatial/`, `associations/`,
`support/<support_id>/cells.parquet`); the live one is UBC v16.43, generation `956c0be7…`. Off-bucket:
the local quarantine `C:/Users/atooz/plantgeo-quarantine/botanical_occurrences/`.

**Re-enabling** (e.g. as a community layer): restore the deleted modules and tests from git history
at `b1745b0f` (the last commit with the full serving + ingestion path; the web map side is in
`src/components/map/AGENTS.md` §Retired herbaria layers), then re-add the layer slug and a binding to
both manifests in both trees (parity-tested).

## Caching

Render order is `tools` → `system` → `messages`, so the breakpoint on the last (only) system
block caches the tool definitions and the system prompt together. `system_prompt()` is
byte-stable by construction: no coordinates, no timestamps, no question; it switches between
`SYSTEM_PROMPT` and `WAVE_TWO_SYSTEM_PROMPT` only on the soil flags, which change only on redeploy. Everything
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
It lists the same `WAREHOUSE_TOOLS` over MCP stdio and calls them, carrying none of that policy, because a
second quiet copy of the sufficiency gate is the way the two surfaces start disagreeing.

Neither surface owns a schema. `agent/llm.py::tool_schemas` reads `.name`, `.description` and
`.input_schema` off the `beta_async_tool` objects themselves and renders the OpenAI
`tools[].function` shape; `mcp_server.tool_descriptors` renames one field to MCP's `inputSchema`. A
parameter added to a tool therefore appears on both surfaces without either being edited, and the
docstring that already documents the caps and the four-state contract is what the model reads.

**A rejected call says which argument broke which rule.** `beta_async_tool` re-raises pydantic's
`ValidationError` as a bare `ValueError("Invalid arguments for function X")`, which a model cannot act
on. A live Gemini eval gave up after one. `llm.py::argument_error_detail` walks the exception's
`__cause__`/`__context__` chain and renders each error as `location: message`, for example
`site_profile.day: Extra inputs are not permitted`. It caps the list at 8 errors and 600 characters,
and it never echoes input values (`include_input=False`; a key name in a location is capped at 60
characters). Three paths use it:

| Path | Where the detail goes |
|---|---|
| `llm.execute_tool_call` | `detail` beside the existing `error` and `tool` keys |
| `routes/agent_tools.py` | `detail` on the 400 `invalid_tool_arguments` body, beside `tool`, `error` and `code` |
| MCP `tools/call` | appended to the `isError` text |

The live map agent sees the bridge's `detail` only if `regional-evidence-tools.ts` relays the 400
body. Today `fetchBoundedJson` throws on any non-2xx, so the model reads a generic "read failed".
The graph's Anthropic runner renders the SDK's `repr(exc)` and is not covered.

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
