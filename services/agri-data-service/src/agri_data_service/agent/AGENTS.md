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

### Where the columns come from

Three of the four tools read `agri.*` and every column is verified against
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
