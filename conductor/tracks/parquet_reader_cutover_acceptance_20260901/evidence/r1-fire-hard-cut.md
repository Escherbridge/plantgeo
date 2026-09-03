---
type: track-evidence
slug: parquet_reader_cutover_acceptance_20260901
artifact: r1-fire-hard-cut
status: authored
---

# r1 — fire hard cut to `wildfire.getFireDetections`

Authoring evidence for acceptance gates 1–3 (and the map-side half of gates 4 and 5). Written by
the implementing slice; the verdict is a separate reviewer's, and no test, lint or build was run
in this lane — see `plantgeo-authoring-and-verification-are-separate-agents`.

## What changed

The map's fire lane no longer has a PostgreSQL path. `LayerManager` and `FireDetails` both read
`wildfire.getFireDetections` through one new hook.

| File | Change |
| --- | --- |
| `src/hooks/useParquetFireDetections.ts` | New. The single fire read: day + bbox + zoom, four terminal states, `truncated`, a latched zoom tier. |
| `src/lib/environmental/parquet-fire-presentation.ts` | New. Cells → `FeatureCollection<Point, FireDetectionCellProperties>`; window totals; a render-safe `resolveZoomTier`. |
| `src/components/map/LayerManager.tsx` | `useFireData` → `useParquetFireDetections`; drawn-day report from the query's own flags; `truncated` and `upstream_unavailable` surfaced on the map overlay. |
| `src/components/panels/FireDetails.tsx` | Count only for `ready`; each refusal rendered as itself. |
| `src/components/map/layers/FireLayer.tsx` | Paint + popup rewritten for the cell vocabulary; NIFC/brightness expressions removed. |
| `src/lib/map/layer-legends.ts` | Fire legend re-derived from the FRP ramp, the no-FRP colour and the two ring colours. |
| `src/lib/server/trpc/routers/wildfire.ts` | Stale "the map is dateless" comment corrected; resolver `signal` threaded to the reader. |

`src/hooks/useFireData.ts` and `src/app/api/fires/route.ts` are untouched and remain on disk with
**no map caller**. They are deleted by a later slice, after parity evidence, per gate 8's
"rollback is an exact known deployment".

## The request shape now sent

Every fire request is `wildfire.getFireDetections` with exactly four inputs:

```
{ bbox: "west,south,east,north",   // useViewportBounds(), the map's own derivation
  date: "YYYY-MM-DD" | undefined,  // useDebouncedLayerDay("fire").requestDate
  zoom: <finite map zoom>,         // useViewportBounds(); the server resolves the rung
  dayRange: 1 }                    // one day, never the rolling lookback
```

- `date` is **omitted** only when the row is positively known to sit on the server's today,
  which is the convention every other slider-day reader uses; sending today's date explicitly
  would mint a second cache entry for one answer.
- `zoom` is passed raw and the rung is resolved server-side by `resolveZoomTier`, so the map and
  the writer can never disagree about which physical partition serves a zoom (gate 4).
- The hook reads all three itself. The map and the panel therefore cannot key two entries for
  one answer — the failure the old lane had structurally, since `/api/fires` took no bbox at
  all and each caller kept its own ETag cache.
- The query is **not issued** when `bbox` is null or when the zoom resolves to no rung; a
  request that cannot be answered is never sent.

Cancellation (gate 5): the procedure now forwards the tRPC resolver's `signal` into
`getParquetFireDetections`. The downstream half of that seam is slice r2b's optional
`signal?: AbortSignal` on `ParquetViewportRead`.

**Update 2026-09-02 — gate 5 is wired on both halves.** When this slice was written the browser
half was inert: `abortOnUnmount` was unset, so tRPC passed `signal: null` for every query and the
resolver `signal` above never fired. Both halves are now closed.

- Browser: `createTRPCReact<AppRouter>({ abortOnUnmount: true })` in `src/lib/trpc/client.ts`.
- Server: `wildfire.getFireDetections` and `wildfire.getWeatherForBbox` wrap their reader in
  `rejectAborted`, which now lives beside the readers in
  `src/lib/server/services/parquet-trpc-readers.ts` rather than in one router. Without it an
  abandoned read still resolves as a 200 `{ kind: "aborted" }` payload, which react-query caches
  against the viewport key and replays to the next reader of that key.
- Pinned by `src/__tests__/trpc/wildfire-cancellation.test.ts`: an aborted read rejects with
  `CLIENT_CLOSED_REQUEST`, and the other six fault kinds still resolve as data so the map can
  caption an outage rather than blank.

One asymmetry the gate should state rather than hide: `httpBatchLink` merges its ops' signals with
`allAbortSignals`, which fires only once EVERY op in the batch has aborted. Cancelling one query's
*server* work early is therefore best-effort; cancelling its *browser-side* result is exact, and
the server-side guard is what makes the difference safe. See
`src/lib/server/services/AGENTS.md` §request-cancellation.

## Gate 3 — truncation is asserted, never absorbed

`ready` carries `truncated`. The hook re-exports it; `LayerManager` renders an amber notice over
the canvas (`data-testid="parquet-layer-unavailable-fire-truncated"`) whenever it is true, and
`FireDetails` says the count is a subset of the viewport. There is no branch that accepts a
truncated fire answer silently, and the old 2,000-row cap — which had no flag at all — is off the
path entirely.

## Terminal states reach the surface

| State | `FireDetails` reads |
| --- | --- |
| `ready` | the detection total, with the cell count and the day beside it |
| `absent` | "No detections published for <day>" + the recorded absence reason |
| `not_generated` | "This day has not been written for the fire lane" / "The fire lane has never been written" |
| `upstream_unavailable` | "Data service unavailable (<fault kind>)" |
| `request_failed` (hook-derived) | "The request failed before returning a typed state" |

No refusal renders `0`. A retained previous-day frame renders `...`, never that day's count under
this day's caption.

## How the track verifies zero live `/api/fires` requests

1. **Static** — `rg "api/fires|useFireData" src/components src/hooks --glob '!useFireData.ts'`
   returns only `src/hooks/AGENTS.md` (the deprecation note) and the legacy hook's own test.
2. **Unit** — `src/__tests__/components/LayerManager.test.tsx` §"issues no request to
   /api/fires while the fire layer is visible" reads the suite-wide `globalThis.fetch` spy after
   rendering with `activeLayers: ["fire"]` and asserts no call URL contains `/api/fires`. It
   watches `fetch`, not the hook module, so any future path to that route fails the case.
3. **Browser (the gate's own evidence, run by the acceptance lane, not here)** — open the
   deployed map with the fire layer on, DevTools ▸ Network filtered to `fires`. Pan, scrub the
   fire row across three days, and cross a zoom breakpoint in each direction. Expected: **zero**
   `/api/fires` entries, and one `trpc/wildfire.getFireDetections` entry per settled
   (day, bbox, rung) whose request body carries all three. A pan must change `bbox` and leave
   `date` alone; a scrub must change `date` and leave `bbox` alone (gate 4).

## Rollback

Revert the commit. `useFireData`, `/api/fires` and `getPublishedFireDetections` are unmodified on
disk, so the revert restores the previous reader with no migration, no data change and no hidden
fallback — the map switches back at deploy granularity, which is what gate 8 asks for. Nothing in
this slice writes to production, and no PostgreSQL fallback was added anywhere in the new path.

## Known follow-ups — state as of 2026-09-02

The two items this section previously listed for `hover-fields.ts` were **already false when this
document was written and are false now**; they are corrected here rather than deleted, because the
gate-3 and gate-5 verdicts were read against them.

- ~~`formatFireDetection` still reads `confidence` / `frp` / `brightness` / `satellite`~~ — it
  does not, and did not at the time: it reads the cell vocabulary
  (`detectionCount`, `highConfidenceDetectionCount`, `frpObservationCount`, `frpSum`,
  `observedDay`, `newestObservedAt`, `zoomTier`).
- ~~The fire hover tooltip renders nothing~~ — it renders. The claim followed from the first one.

What was actually true and is now closed: the tooltip and the click popup each carried their own
copy of those six fields and had drifted into two renderings of one cell ("not reported" against
"Not reported", `1234.6 MW` against `1,234.6 MW`). Both now call
`fireDetectionCellLines` in `src/lib/map/fire-cell-caption.ts`, and `FireLayer`'s circle paint
tests the same `frpObservationCount > 0 && frpSum != null` condition the caption does, so a null
sum can no longer paint as 0 MW while the caption says "Not reported".

Still open, deliberately:

- Cell rendering as density polygons remains out of scope: cells stay points this wave. The gap is
  recorded as `shippedDeviation` on the vegetation entry of `src/lib/map/layer-render-contract.ts`
  (the fire lane's own detail band is `aggregate_cell` and is honest as drawn).

## Gate 3 — evidence

`data-testid="parquet-layer-unavailable-fire-truncated"` is asserted by
`src/__tests__/components/LayerManager.test.tsx`, alongside a case each for
`upstream_unavailable`, `absent` (governed absence, reason quoted) and `not_generated`. When this
document first cited the truncation testid as gate-3 evidence, no test rendered it.
