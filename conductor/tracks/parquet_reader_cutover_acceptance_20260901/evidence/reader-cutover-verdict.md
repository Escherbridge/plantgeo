---
type: track-evidence
slug: parquet_reader_cutover_acceptance_20260901
artifact: reader-cutover-verdict
status: authored
---

# Reader cutover — authoring verdict against gates 1–9

What the reader lane can prove from the tree, and what it cannot prove without production.
Written by the implementing slice (r3/r4); the reviewer verdict is separate, and no test, lint or
build was run in this lane — see `plantgeo-authoring-and-verification-are-separate-agents`.

The short version: **gates 1–6 and 8 are provable from the tree today. Gates 7 and 9 are not, and
neither is the production half of gates 1 and 4.** Everything still open needs a browser or a
request trace against a deployment and belongs to `parquet_production_acceptance_20260901`.

## The exact commits

| Commit | What it is |
| --- | --- |
| `2b4cfef` | Wave 1. The fire hard cut itself: `useParquetFireDetections`, the cell presentation layer, `LayerManager`/`FireDetails`/`FireLayer` repointed, `wildfire.getFireDetections` given the resolver `signal`, and the availability-backed capability read. Documented in `evidence/r1-fire-hard-cut.md`. |
| `9052998` | Wave-1 verification closure. The findings two adversarial reviews raised against `2b4cfef`, plus the handoff record. This is the commit the deletion below sits on top of. |
| this wave (uncommitted at authoring time) | Legacy removal: the route, the hook and their suites deleted; the AGENT's fire read moved off PostgreSQL; the `causalTauEst` submission default removed. Listed file by file under "What this wave changed". |

**Rollback is a revert of the range `2b4cfef..HEAD`**, not a flag and not a fallback. See
"Rollback" below for why that is now the only mechanism and why that is what gate 8 asked for.

## What this wave changed

| File | Change |
| --- | --- |
| `src/hooks/useFireData.ts` | **Deleted.** |
| `src/app/api/fires/route.ts` | **Deleted** (the `src/app/api/fires/` directory with it). |
| `src/__tests__/hooks/useFireData.test.ts` | **Deleted** with its subject. |
| `src/__tests__/api/fires-route.test.ts` | **Deleted** with its subject. |
| `src/lib/server/services/regional-context.ts` | The agent's fire read repointed from `getPublishedFireDetections` to `getParquetFireDetections`; the four reader states mapped into the existing `ViewedDateReadOutcome` vocabulary; the payload block moved to the cell grain the lane actually writes. |
| `src/lib/server/services/environmental-read-model.ts` | Doc only: `getPublishedFireDetections` is now labelled the alert engine's PostgreSQL read, not a map or agent reader. |
| `src/lib/server/trpc/routers/interventions.ts` | The fabricated `causalTauEst ?? 0.15` submission default removed. |
| `src/lib/net/request-budget.ts`, `src/lib/net/AGENTS.md`, `src/hooks/AGENTS.md`, `src/lib/server/AGENTS.md` | Docs repaired to stop naming deleted paths; no `"fires"` request-budget lane existed to remove (lanes there are plain strings, created on use). |
| `src/__tests__/services/regional-temporal-context.test.ts`, `src/__tests__/api/interventions-trpc.test.ts` | New cases for the two behaviour changes above. |
| `src/__tests__/services/parquet-plane-client.test.ts`, `src/__tests__/services/environmental-time.test.ts` | Comment-only: both cited `/api/fires` as a live path. |

## Gate by gate

### 1. No production fire pixel request reaches `/api/fires` — PROVEN in the tree, production evidence outstanding

The route no longer exists, so this is now structural rather than behavioural: there is nothing
to reach. Before deletion it was already caller-free, pinned by
`src/__tests__/components/LayerManager.test.tsx` §"issues no request to /api/fires while the fire
layer is visible", which reads the suite-wide `globalThis.fetch` spy after rendering with
`activeLayers: ["fire"]`. That case is deliberately kept: it watches `fetch`, not a module, so it
still fails if any future code path reaches for the path string.

Static proof: `rg "api/fires|useFireData" src/` now returns only past-tense prose in
`src/hooks/AGENTS.md`, `src/lib/net/AGENTS.md`, `src/lib/server/AGENTS.md` and four test comments.
No `src/**/*.ts(x)` executable line names either identifier.

**Still needed from production:** the DevTools trace in `r1-fire-hard-cut.md` §"How the track
verifies zero live `/api/fires` requests", item 3. Deletion makes it a formality, not a risk.

### 2. Every fire request contains the settled day, viewport bbox and zoom — PROVEN

- The procedure cannot be called without a zoom: `zoom: mapZoomSchema` is non-optional on
  `wildfire.getFireDetections`' input (`src/lib/server/trpc/routers/wildfire.ts:87`, inside the
  `z.object` at `:83-88`).
- The hook reads all three itself rather than taking them as props, so the map and the panel
  cannot key two entries for one answer: `src/hooks/useParquetFireDetections.ts:90-100`.
- Tests: `src/__tests__/hooks/useParquetFireDetections.test.ts` §"sends the fire row's day, the
  viewport bbox and the viewport zoom as one key", §"omits the date for a row sitting on the
  server's today, keeping one cache entry", §"asks for nothing before the viewport can name a
  bbox", §"asks for nothing at a zoom no published rung serves";
  `src/__tests__/components/LayerManager.test.tsx` §"passes the same finite viewport zoom to every
  Parquet-backed map read".

The agent's read now satisfies the same shape for the same reason —
`src/__tests__/services/regional-temporal-context.test.ts` §"scopes the agent's fire read to its
own window and to one published rung" asserts the assembler's own 0.5-degree box and its declared
`CONTEXT_MAP_ZOOM`.

### 3. No unlabelled row cap; accepted responses assert `truncated=false` — PROVEN

The old 2,000-row cap had no flag at all and is off the path with the route. `ready` carries
`truncated`, the hook re-exports it, and both surfaces render it:
`src/__tests__/components/LayerManager.test.tsx` asserts
`data-testid="parquet-layer-unavailable-fire-truncated"`, and §"says a truncated read is a subset
of the viewport" covers the panel. The hook's own case is
`src/__tests__/hooks/useParquetFireDetections.test.ts` §"surfaces a truncated read instead of
drawing the subset as the whole viewport". No branch accepts a truncated fire answer silently.

The agent read carries the same bit: `RegionalContextPayload.fireDetections.truncated`, asserted
by §"reports published cells at the grain the lane writes, newest first and never as pixels".

### 4. Pan changes bbox only; scrub changes day only; zoom selects exactly one rung — PROVEN in the tree, browser evidence outstanding

- One rung per zoom, resolved server-side from the raw zoom by the single ladder in
  `src/lib/map/zoom-tiers.ts`, so the map and the writer cannot disagree. Boundary cases:
  `src/__tests__/lib/environmental/parquet-fire-presentation.test.ts` §`servingZoomTierForMapZoom`
  (z3→z0, z11.4→z9, z13→z13, z22→z13, non-finite→null).
- Day and bbox are independent inputs of one query key, so neither can move the other:
  `src/__tests__/components/LayerManager.test.tsx` §"moves both zoom-adaptive queries onto a new
  tier when the viewport zooms" and §"moves only the scrubbed layer's feed, leaving every other
  feed on its own day".

**Still needed from production:** the pan/scrub/breakpoint walk in the browser. A unit test proves
the inputs are separable; only the trace proves the deployed map separates them.

### 5. Superseded requests stop server work as well as browser work — PROVEN, with one stated asymmetry

Both halves are wired: `createTRPCReact<AppRouter>({ abortOnUnmount: true })` in
`src/lib/trpc/client.ts` (without it tRPC passed `signal: null` and the resolver signal never
fired), and `rejectAborted` around the reader in `wildfire.getFireDetections`. Pinned by
`src/__tests__/trpc/wildfire-cancellation.test.ts`: an aborted read rejects with
`CLIENT_CLOSED_REQUEST`, and the other six fault kinds still resolve as data so the map can
caption an outage rather than blank.

The asymmetry, stated rather than hidden: `httpBatchLink` merges its ops' signals with
`allAbortSignals`, which fires only once EVERY op in the batch has aborted. Cancelling one query's
*server* work early is best-effort; cancelling its *browser-side* result is exact, and the
server-side `rejectAborted` guard is what makes the difference safe — an abandoned read otherwise
resolves 200 with an `{ kind: "aborted" }` payload that react-query caches against the viewport
key.

### 6. Capability ceilings are source-specific, never future-relative; `Latest` is a terminal day — PROVEN

`src/__tests__/services/parquet-slider-capabilities.test.ts`: §"closes the tail at the lane's own
source horizon, not at the census day", §"withholds a rung holding a day past its own source
ceiling rather than clamping it", §"accepts a latest day that sits exactly on the ceiling",
§"authors an ungoverned tail when a product ends before the server current day", §"owes nothing at
all when the lane already holds through its own ceiling".

### 7. Cold/warm catalogue time, day-row TTFB and request-to-paint separately measured — NOT PROVEN

No timing evidence exists in this lane, and none can be authored here: all three are wall-clock
measurements against a deployment. `plan.md` Wave R0 item 1 ("capture one cold/warm timing packet
at coarse, middle and detail zoom") is still open and is the same work.

**Owned by `parquet_production_acceptance_20260901`.** The reader lane's contribution is that the
three numbers are now separable at all — cold catalogue, day-row and paint are three distinct
requests since the availability-index read stopped being a coverage walk.

### 8. Rollback is an exact known deployment, never a hidden PostgreSQL fallback — PROVEN, and strengthened by this wave

`r1-fire-hard-cut.md` could only claim the weaker form of this: the legacy reader sat on disk
unmodified, so a revert restored it. That is now stronger in both halves.

- **Exact deployment:** rollback is a revert of `2b4cfef..HEAD`. There is no flag, no env switch
  and no per-request branch anywhere in the fire path; the map switches back at deploy
  granularity, which is precisely what the gate asks for.
- **No hidden fallback:** the PostgreSQL fire reader is no longer reachable from ANY request path.
  `getPublishedFireDetections` survives with exactly one caller,
  `src/lib/server/services/alert-engine.ts:209` — a server-side alert job that reads PostgreSQL by
  design and serves no map or agent answer. Its doc comment now says so, as does
  `src/lib/server/AGENTS.md` §slider-day.
- The last request-time PostgreSQL fire read closed with it. `regional-context.ts` — the AGENT's
  read — called `getPublishedFireDetections` at request time until this wave; the runbook forbids
  exactly that for map and agent answers. It now calls `getParquetFireDetections`, and no branch
  falls back: an outage resolves to `read_failed` in the temporal vocabulary and the payload block
  is null. §"treats an outage returned as a state, not a rejection, as a failed read" is the
  regression pin, and it exists because the Parquet readers return an outage as DATA — the
  `status === "rejected"` check every neighbouring source in that assembler uses would have read a
  down warehouse as a published-and-empty day.

### 9. A cold capability request performs one availability-pointer GET and one availability Parquet GET per lane, with zero historical listings or data-part reads — PARTIALLY PROVEN

Proven in the tree: the capability path reads the availability artifact rather than walking
prefixes, and fails closed when a lane has not published its index.
`src/__tests__/services/parquet-slider-capabilities.test.ts` §"asks availability before currency,
so one lane's unpublished index is not read as a stale census", §"drops even the PostgreSQL
passthrough row when its Parquet lane withholds its index", §"reports an availability-backed row
as such when every rung read the index", §"does not let the retired PostgreSQL stream scan remount
a withheld Parquet slider"; and `src/__tests__/services/parquet-plane-client.test.ts` §"decodes an
availability-backed census and the lane that withheld itself inside it", §"keeps a
census-authority lane distinguishable from an availability-backed one".

**Not proven:** the request COUNT. "One pointer GET and one Parquet GET per lane, zero LIST" is a
claim about observed traffic, and no test in this lane counts object-store operations against a
real bucket. `plan.md` Wave R2 item 2 ("trace cold and warm capability reads and prove zero
historical LIST/data-part operations") is the outstanding work.

**Owned by `parquet_production_acceptance_20260901`,** together with gate 7.

## The request shape now sent

Unchanged from `r1-fire-hard-cut.md` for the map, and now matched by the agent.

```
map    trpc.wildfire.getFireDetections
       { bbox: "west,south,east,north",   // useViewportBounds()
         date: "YYYY-MM-DD" | undefined,  // useDebouncedLayerDay("fire").requestDate
         zoom: <finite map zoom>,         // resolved server-side by resolveZoomTier
         dayRange: 1 }

agent  getParquetFireDetections (direct call, no HTTP hop — the assembler has no session to
       route through)
       { bbox: "<lon-0.25>,<lat-0.25>,<lon+0.25>,<lat+0.25>",
         date: <the fire row's viewed day> | undefined,
         mapZoom: 9,                      // CONTEXT_MAP_ZOOM: the rung a 0.5-degree box is
         dayRange: firmsDayRange() }      // the old PostgreSQL lookback; forced to 1 by the
                                          // reader whenever a day is named
```

`date` is omitted only when the row is positively known to sit on the server's today — sending
today's date explicitly would mint a second cache entry for one answer.

### How the agent's four states land in the temporal vocabulary

`regional-context.ts` already had a state vocabulary for exactly this problem, so the mapping is
onto `ViewedDateReadOutcome` rather than a new one:

| Reader state | Agent outcome | Why |
| --- | --- | --- |
| `ready`, cells present | `observed_on_viewed_date` | — |
| `ready`, no cells | falls to the coverage record | The day published; whether anything was here is what the record answers. |
| `absent` (governed absence) | falls to the coverage record | A governed absence breaks published continuity, so the capability row is the conservative judge. Never asserted directly. |
| `not_generated` | `not_published_on_viewed_date`, with the reader's own reason | The one case that OVERRIDES the coverage record. A capability row that still lists the day as published would otherwise license "no fires here" for a partition nobody wrote. |
| `upstream_unavailable`, or the reader raising | `read_failed` | A fault is never an absence. |

The override is carried by one new optional field, `SourceReadState.notPublishedReason`. The
PostgreSQL readers leave it unset — an empty result is all they can say — so every non-fire source
resolves exactly as it always has.

## Rollback

`git revert` of `2b4cfef..HEAD`. That restores `useFireData`, `/api/fires`, the agent's
PostgreSQL fire read and the two deleted suites in one operation, with no migration, no data
change and no schema step, because nothing in this range touched the database. Production
switches back at deploy granularity.

What rollback does NOT need, and deliberately has no mechanism for: a runtime fallback. There is
no code path that reaches PostgreSQL for a fire pixel or a fire answer if Parquet is unavailable —
that is gate 8's actual requirement, and it is now enforced by the absence of the code rather than
by a convention.

One caveat worth stating for whoever executes it: reverting `2b4cfef..HEAD` also reverts the
`causalTauEst` default removal and the availability-index capability work, which are in the same
range but not part of the fire lane. A fire-only rollback is a revert of the fire files
specifically, not of the range.

## Open, and owned elsewhere

- **Gates 7 and 9's production halves**, and the browser halves of gates 1 and 4 —
  `parquet_production_acceptance_20260901`.
- **`coverageAuthority` / `sourceCeilingDay` in the slider caption** — published by the capability
  service, no UI consumer yet. A row read from an object-store walk still captions identically to
  one proved from the checksummed availability index. `plan.md` Wave R2.
- **Existing `causalTauEst = 0.15` rows** — suspect, unadjudicated, and owned by
  `repository_conformity_hardening_20260901`. No migration ships here on purpose: rewriting the
  rows would destroy the only signal separating the suspect population from honestly-absent ones.
  See `src/lib/server/AGENTS.md` §community-submission-tau.
