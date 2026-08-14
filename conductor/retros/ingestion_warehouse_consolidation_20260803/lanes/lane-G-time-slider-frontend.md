---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: G
status: built-pending-integration
depends_on: none
launched_at: 2026-08-03
---

> **Orchestrator rulings, 2026-08-03** — §7 open questions 1, 3 and 4 are settled; the
> boundary below is widened accordingly. See §8 for the record and the prod evidence.

# Lane G — Time slider front end

Read [`lanes/README.md`](./README.md) first (file boundaries + inherited rules), then
[`plans/…-2026-08-03.md` §6](../../../../plans/ingestion-warehouse-consolidation-2026-08-03.md)
for the design this implements. This brief does not restate them.

## 1. Goal

The map gains one continuous, **day-granular** time slider running from the earliest ingested
day, through today, to today + 30. Scrubbing to any day re-renders the active layers at that
date. A variant toggle (statistical Monte Carlo | ML) is enabled **only for days after today**;
the ML option is present but disabled until phase 7. There are no 5/10/30-day horizon buttons —
the slider *is* the horizon control (D2, [`../spec.md`](../spec.md):28). When this lane is done, all of that
works end-to-end against a **typed contract and mock data**, with zero schema changes and zero
server code written: swapping the mock for lane J's real `environmental.getMetricAtDate` is a
one-line change of the query function, and nothing else moves.

## 2. Prerequisites

None. This lane is startable now — it adds no migration and reads no new table.

Verify the tree is where this brief expects before starting:

| Check | Command | Expected |
|---|---|---|
| On `main`, clean | `git status --short` | no output |
| No slider work exists yet | `grep -rn "getMetricAtDate\|metricDaily\|selectedDate\|forecastVariant" src/` | no matches |
| Baseline is green | `npm run type-check` | exits 0, no errors |

If the third command already fails, stop and report — do not start on a red baseline.

You do **not** need Postgres, podman, Martin or Redis for this lane. `npm run dev` renders the
map against whatever the deployed API returns; the slider itself runs on mock data.

## 3. Files you own

Exactly the lane-G row of [`lanes/README.md`](./README.md) §"File boundaries". **This list is
post-ruling** — §8 widened it on 2026-08-03 and §7 questions 1, 3 and 4 are closed:

- `src/types/time-slider.ts` *(net-new — the contract types live here, **not** in the store; §8 ruling 4)*
- `src/stores/time-slider-store.ts` and the rest of `src/stores/**`
- `src/components/map/TimeSlider.tsx`  *(net-new)*
- `src/components/map/LayerManager.tsx` *(granted by §8 ruling 1 — make its four dateless tRPC calls date-aware)*
- `src/components/panels/**`
- `src/__tests__/stores/**` — in practice `src/__tests__/stores/time-slider-store.test.ts` *(net-new)*
- `src/__tests__/components/**` — in practice `src/__tests__/components/TimeSlider.test.tsx` *(net-new dir)*
- **delete** `src/components/ui/time-slider.tsx` *(§8 ruling 3 — zero importers, animating design contradicts D2)*

Nothing else under `src/__tests__/` is yours: lane B owns
`src/__tests__/lib/geometry-migration.test.ts` and lane D deletes
`src/__tests__/api/cron-ingest.test.ts` and `src/__tests__/services/ingestion-jobs.test.ts`.

Still **not** yours, still hand-offs: `src/components/map/MapView.tsx` (the one-line mount) and
`src/components/map/Legend.tsx`. See §8 "Boundary as launched" for what that defers.

**Other sessions are running concurrently against this same working tree.** Do not touch
anything outside the list — in particular not `src/lib/server/db/**`, not `drizzle/**`
(both are lane B/J), and not `src/lib/server/services/environmental-read-model.ts` or
`src/lib/server/trpc/routers/environmental.ts` (lane J). Lane H is doing browser QA in
`src/app/**` and `src/components/**` at the same time, and is under instruction not to edit
`src/stores/**`, `src/components/panels/**` or `TimeSlider.tsx`; if you see edits there that
are not yours, stop and report. If you need something outside your list, stop and report
rather than reaching across.

`src/components/map/LayerManager.tsx` is the natural consumer and **is now yours** (§8 ruling 1).
Lane H has been told not to touch it while this lane is in progress.

## 4. The work

### Step 1 — Write the contract, in `src/types/time-slider.ts`

**Superseded by §8 ruling 4: these types live in `src/types/time-slider.ts`, not in the store.**
Lane J implements them server-side and imports that one file; `time-slider-store.ts` imports it
too and re-exports nothing.

```ts
/** One geo.metric_daily row joined to its geometry version, as the map consumes it. */
export interface MetricAtDateFeature {
  geometryId: string;
  geometry: GeoJSON.Geometry;
  medianValue: number;
  lowValue: number | null;   // p10 on ML; null on observations
  highValue: number | null;  // p90 on ML; null on observations
  valueKind: "observed" | "forecast";
  variant: "observed" | "monte_carlo" | "ml";
  issuedOn: string;          // YYYY-MM-DD
  provenanceKey: string;
}

/** Why a layer has nothing to draw. Never collapse these into an empty collection. */
export type MetricAtDateAvailability =
  | "published"
  | "not_yet_observed"     // date precedes this layer's earliest version (risk 7b)
  | "not_forecastable"     // temporal_kind='event' and date is in the future
  | "beyond_horizon"       // date > serverCurrentDate + forecastHorizonDays
  | "variant_unavailable"  // requested variant absent from forecastVariants
  | "not_published";

export interface MetricAtDateCollection extends GeoJSON.FeatureCollection {
  availability: MetricAtDateAvailability;
  reason: string | null;
}
```

This deliberately mirrors `PublishedDroughtCollection`
(`src/lib/server/services/environmental-read-model.ts:357-361`), which already carries
`availability` / `observedAt` / `reason` on a GeoJSON collection. **Reuse that shape; do not
invent a second vocabulary for "no data".**

Query input matches the settled procedure signature:
`environmental.getMetricAtDate({ metric, date, variant, bbox })`.

### Step 2 — The capabilities contract (this is where "today" comes from)

Open question 7 is settled: **one server-supplied current date, in UTC**, delivered with the
layer capabilities, so the hatched region, the toggle's enabled state and the query predicate
cannot disagree.

```ts
export interface SliderLayerCapability {
  layerName: string;                                  // geo.layers.name
  temporalKind: "snapshot" | "daily_series" | "event";
  forecastHorizonDays: number;                        // 0 = not forecastable
  forecastVariants: Array<"monte_carlo" | "ml">;
  earliestObservedDate: string | null;                // min(version_valid_from)::date
}

export interface SliderCapabilities {
  serverCurrentDate: string;              // server UTC today; the ONLY definition of "today"
  layers: SliderLayerCapability[];
}
```

`earliestObservedDate` and `serverCurrentDate` are **always read from this payload**.
`new Date()` on the client is forbidden anywhere that decides slider domain, hatching, toggle
state or a query date — the browser's clock is the wrong clock and disagrees by up to a day.
Slider depth is likewise never a constant.

Per open question 6, these three per-layer fields will be **columns on `geo.layers`**
(`src/lib/server/db/schema.ts:149-163` today has none of them); lane J writes that migration.
Code against them as if they exist.

### Step 3 — Store fields and actions

Extend the slider store (a new store, not `map-store.ts` — keep `MapState` at
`src/stores/map-store.ts:7-26` about camera and layer visibility, which is what
`src/components/map/AGENTS.md:5-11` says it is for):

| Field | Type | Notes |
|---|---|---|
| `selectedDate` | `string` (`YYYY-MM-DD`) | initialised to `serverCurrentDate` once capabilities land |
| `forecastVariant` | `"monte_carlo" \| "ml"` | `"monte_carlo"` default; `"ml"` selectable but disabled |
| `capabilities` | `SliderCapabilities \| null` | |

Actions: `setSelectedDate(date)`, `setForecastVariant(variant)`, `setCapabilities(caps)`,
`resetToToday()`.

Derived selectors (pure functions, exported and unit-tested — this is where the logic lives,
not in the component):

- `sliderDomain(caps)` → `{ firstDay, today, lastDay }` where
  `firstDay = min(earliestObservedDate across layers)` and
  `lastDay = serverCurrentDate + max(forecastHorizonDays)`. Both **dynamic**, never literals.
- `isFutureDate(date, caps)` → drives hatching and toggle enablement.
- `layerAvailabilityAt(layer, date, variant, caps)` → returns a `MetricAtDateAvailability`
  **client-side, before querying**, so an event layer under a future date short-circuits to
  `"not_forecastable"` and never issues a request.

### Step 4 — `src/components/map/TimeSlider.tsx`

Discrete-day control. Requirements, each of which is a behaviour to get right, not a style note:

1. **Step is exactly one day.** Model position as an integer day offset from `firstDay`;
   convert to a date string at the edges only. Do not use fractional slider values.
2. **No animation, no play button, no interpolation between days** (§6 "Two prohibitions",
   `plans/…:704`). Day 7 and day 8 are separate rows; tweening invents values.
3. **A labelled tick at today**; the segment after it renders **hatched**.
4. **Variant toggle**: disabled for `date <= serverCurrentDate` with the tooltip
   "forecasts apply to future dates"; `ml` rendered as an option but `disabled` with a reason
   ("no trained model yet") until phase 7.
5. **Debounce on settle, not on every tick.** Reuse `useDebounce`
   (`src/hooks/useDebounce.ts:3`) — the store updates immediately so the thumb tracks the
   pointer, and the *query* reads the debounced value. This is the client half of risk 6.
6. Mount it as a sibling of `MapControls` in the map chrome. **You do not own
   `src/components/map/MapView.tsx`** (`:292-300` is where controls mount) — build the
   component and its stories/tests, and report the one-line mount as a hand-off.
7. Popup and label markup must not hard-code text colours; use the existing tokens and the
   `.map-popup-meta` class (`src/components/map/AGENTS.md:23-25`).

### Step 5 — The query hook, with a mock implementation

Write `useMetricAtDate(...)` inside the store module (or a co-located hook file under
`src/stores/`). It must:

- Short-circuit on `layerAvailabilityAt(...)` before issuing anything.
- Key on `(metric, debouncedDate, variant, bbox)`.
- **Prefetch only the ±7-day neighbourhood** of the current selection, with a short
  `staleTime`, and let anything older fall out of cache. This bounds the client mirror of
  risk 6 (`plans/…:794`): 400 observed days + 30 forecast days is ~430 keys per layer per
  variant if keyed naively.
- Resolve through a swappable `fetchMetricAtDate` function. Ship the mock as the default and
  leave a one-line `// lane J: replace with trpc.environmental.getMetricAtDate.useQuery`
  seam. **The mock must live in the test files or behind an explicit dev-only guard — it must
  not be reachable in a production build.** Fabricated values reaching the map violates
  `src/components/map/AGENTS.md:3` ("unavailable data must remain visibly unavailable rather
  than producing substitute recommendations") and the project's standing rule that empty
  layers are deliberate governance stubs (`lanes/README.md` §"Rules every lane inherits").

### Step 6 — Band rendering and honest degradation

Encoding table is `plans/…:696-702`. The four channels: colour = `medianValue` on the metric's
existing ramp; **opacity = `(highValue - lowValue)` normalised across the visible extent** so a
wide band washes out; forecast days draw a **soft/dashed** outline where observed days draw a
solid one; the popup carries `low` / `median` / `high`, `issuedOn`, `variant` and
`provenanceKey`. Add a band-width key to the legend when the slider is in the future.

**No isolines or contouring on forecast days** — a contour is a precision claim the band does
not support.

Degradation is **visible, never silent**. Each non-`published` availability renders a legend
message and greys the layer row; it does **not** hide the layer and does **not** keep drawing
today's data under a future date label:

| Availability | Message |
|---|---|
| `not_yet_observed` | "Not yet observed at this date" |
| `not_forecastable` | "Fire detections are events; no forecast exists" |
| `beyond_horizon` | "No forecast beyond +N days" |
| `variant_unavailable` | "ML forecast not available for this layer" |

The legend already translates DB layer names to toggle ids
(`src/components/map/Legend.tsx:16-23`); consume that vocabulary, do not add a second one.

### Step 7 — Panels

`src/components/panels/**` is yours. Where a panel shows a "latest" value (e.g. `WaterPanel`,
`VegetationPanel`), have it read `selectedDate` from the store so the panel and the map cannot
disagree about which day is displayed. Do not add date parameters to tRPC calls that lane J
has not shipped yet — read from the same hook.

## 5. Traps specific to this lane

| # | Trap | Evidence |
|---|---|---|
| 1 | **A `TimeSlider` already exists, it is the wrong one, and you are deleting it.** `src/components/ui/time-slider.tsx:16` exports a component with a **play button, `requestAnimationFrame` tweening and `step = (max - min) / 1000`** — continuous interpolation, i.e. a direct D2 violation. It has **zero importers** (re-confirmed at launch). §8 ruling 3: delete it. Until you do, do not import it, do not extend it, and do not let your new export collide with it in a barrel file. Re-run `grep -rn "ui/time-slider\|<TimeSlider" src/` immediately before deleting — lane H is editing under `src/components/**` concurrently. | `src/components/ui/time-slider.tsx:25-49,85` |
| 2 | **Martin cannot carry a date.** All four declared function sources take `(z,x,y)` only, so the slider layer is **GeoJSON over tRPC, not MVT**. Do not attempt a date-templated MVT source. Existing MVT layers are untouched by this whole plan. | `infra/martin/martin.yaml:27-39` |
| 3 | **A `style.load` handler must register exactly once per map.** An effect listing `selectedDate` (or bbox corners, or `activeLayers`) in its deps tears down and re-registers its listener on every scrub, moving it to the back of the queue and **inverting layer stacking order**. Read changing inputs from a ref; register with `[map]`-shaped deps only; apply the change in a separate cheap effect. Scrubbing a day-granular slider fires this far more often than anything shipped so far. | `src/components/map/AGENTS.md:19-21` |
| 4 | **An empty feed is not a toggle being off.** A layer whose feed returns nothing stays mounted with an empty source. Never unmount a layer because the selected date has no data — that is indistinguishable from the user hiding it. | `src/components/map/AGENTS.md:9` |
| 5 | **Never derive slider depth from `geo.features.created_at`.** The refresh-in-place path rewrites it, so all rows read as created today. Depth comes only from `earliestObservedDate` in the capabilities payload (`min(version_valid_from)`). | `lanes/README.md` §"Rules every lane inherits"; `plans/…:789` (risk 2c) |
| 6 | **`setStyle()` fires `style.load` synchronously on the diff path**, so a `once("style.load", …)` registered *after* `setStyle` never runs — and the diff silently emits `setTerrain(undefined)` / `setProjection(undefined)`. If the slider ever triggers a style change, register the restore handler *before* the call. | `src/components/map/AGENTS.md:15` |
| 7 | **Do not build a forecast-comparison view, skill-score dashboard, or model-registry UI.** Evaluation lives in the `agri` CLI. Out of scope. | `plans/…:735` |
| 8 | **No external URLs in `src/stores/**` or `src/components/**`.** `npm run check:data-boundary` scans those roots against an allowlist and fails the build on anything else — a mock pointing at a public API will be caught. | `scripts/check-client-provider-urls.mjs:5-33` |

## 6. Definition of done

One sweep at the end — no test → fix → test loop.

```powershell
cd <repo-root>
npm run type-check
npm run lint
npm run check:data-boundary
npm test
```

Proves success:

| Command | Expected |
|---|---|
| `npm run type-check` | exits 0, no output |
| `npm run lint` | 0 errors |
| `npm run check:data-boundary` | exits 0 (no new external origin, no client→server import) |
| `npm test` | all pass; **299 was the last recorded green baseline** (`plan.md:204`) — expect that plus your new cases. Treat the number as orientation only, per `lanes/README.md` §"Rules every lane inherits". Assert *no regressions*, not a literal total. |

Your new tests must cover, at minimum:

1. `sliderDomain` is computed from the capabilities payload — feed it two layers with different
   `earliestObservedDate` / `forecastHorizonDays` and assert both ends move. A test that would
   still pass with a hardcoded domain is not a test.
2. `layerAvailabilityAt` returns `"not_forecastable"` for `temporalKind: "event"` at
   `serverCurrentDate + 1`, and `"not_yet_observed"` for a date before `earliestObservedDate`.
3. The variant toggle is disabled for every date `<= serverCurrentDate` and enabled after it,
   with `serverCurrentDate` taken from the payload — assert with a `serverCurrentDate` that is
   deliberately **not** the machine's own date, so a stray `new Date()` fails the test.
4. `"ml"` is present in the options and `disabled`.
5. Scrubbing across N days issues at most one query after the debounce settles.

Also confirm by hand once: `npm run dev`, scrub the slider past today, and check that an event
layer shows its greyed reason rather than freezing at today's data. Browser QA proper is
lane H's job — do not expand into it.

## 7. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | ~~Nobody owns `src/components/map/LayerManager.tsx`.~~ **Answered — see §8 ruling 1.** It holds the four dateless tRPC calls (`:60-82`: `getDroughtClassification`, `getStreamflow`, `getGroundwater`, `wildfire.getWeatherForBbox`) that §6 says must each take a date. | **Granted: lane G owns it.** Make the four calls date-aware from `selectedDate`. Lane H is under instruction not to touch it while this lane is in progress; if you find edits there that are not yours, stop and report. |
| 2 | ~~Test paths are outside the declared boundary.~~ **Settled on review.** Store tests live in `src/__tests__/stores/` (`map-store.test.ts` etc.), not under `src/stores/`. | `src/__tests__/stores/**` and `src/__tests__/components/**` are now in lane G's row of the boundary table. Use them; no escalation needed. |
| 3 | ~~What happens to the dead `src/components/ui/time-slider.tsx`?~~ **Answered — see §8 ruling 3.** Zero importers, and its animating/interpolating design contradicts D2. | **Delete it.** Per the spec's standing instruction, "clean up dead code as you touch it" ([`../spec.md`](../spec.md):38). Do not ask again; two exports named `TimeSlider` with opposite semantics is the trap this removes. |
| 4 | ~~Where do the contract types finally live?~~ **Answered — see §8 ruling 4.** Lane J needs to import them server-side. | **`src/types/time-slider.ts`, from the start** — not the store file (`src/types/map.ts` is the precedent). §4 step 1 still says "types live in the store file"; that is superseded. `time-slider-store.ts` imports from `src/types/time-slider.ts` and re-exports nothing. |
| 5 | **There is no grid yet, so areal metrics have no geometry to render.** Plan open question 1: `agri.spatial_cell` has 0 rows and no grid is defined, so after lane B's backfill `geo.geometry` holds only points and perimeter polygons. | Does not block this lane — mock cells and build against the contract. **But do not fabricate cells in any non-test code path**, and do not assume a cell count when sizing the GeoJSON payload. Plan open question 2 (whether GeoJSON stays sufficient, or a `(z,x,y,query)` Martin function becomes necessary) turns on that grid choice; flag it if your mock suggests tens of thousands of cells at full-bbox zoom. |
| 6 | **`purpose = 'serving'` filtering** — not actually open. D4 is **settled** ([`../spec.md`](../spec.md):30): `purpose` stays a plain column and the *serving query* filters it, so a backtest cannot surface as a live forecast (`plans/…:411`). | Server-side only; no front-end consequence. Do not surface `purpose` in the UI, and do not add a client-side `purpose` filter — the filter is the server's and duplicating it would let the two drift. |

## 8. Orchestrator rulings and launch record — 2026-08-03

Lane launched this date. §7 questions 1, 3 and 4 were escalated and answered by the owner.

| # | Ruling | Consequence for the boundary |
|---|---|---|
| 1 | **Granted.** Lane G owns `src/components/map/LayerManager.tsx`. | Added to §3. Lane G makes the four dateless tRPC calls date-aware. **Collision risk:** §7 Q1 notes lane H may edit under `src/components/**` for small fixes — lane H must not touch `LayerManager.tsx` while lane G is in progress. |
| 3 | **Delete.** `src/components/ui/time-slider.tsx` is removed. | Added to §3 as a deletion. Confirmed zero importers at launch (`grep -rn "TimeSlider" src/` matched only the file's own three lines). |
| 4 | **`src/types/time-slider.ts`.** | Added to §3. Contract types live there from the start, not in the store file; lane J imports that one definition rather than restating it. `src/stores/time-slider-store.ts` re-exports nothing — it imports. |

### Boundary as launched

Owned: `src/types/time-slider.ts`, `src/stores/**`, `src/components/map/TimeSlider.tsx`,
`src/components/map/LayerManager.tsx`, `src/components/panels/**`,
`src/__tests__/stores/**`, `src/__tests__/components/**`, and the deletion of
`src/components/ui/time-slider.tsx`.

Still **not** owned, still hand-offs: `src/components/map/MapView.tsx` (the one-line mount)
and `src/components/map/Legend.tsx`. The legend consequence of §6 is therefore deferred:
availability messaging renders inside lane G's own components keyed by `geo.layers.name`
— the DB vocabulary, upstream of the toggle-id translation, so it is not a second vocabulary.
Hand-off: `Legend.tsx` should export `TOGGLE_ID_BY_LAYER_NAME` (`:16-23`) so the band-width
key can move into the legend proper.

### Prod evidence gathered at launch

Read-only against the Railway production database (`switchback.proxy.rlwy.net:37967/plantgeo`,
PostgreSQL 18.4), per the owner's "run against prod, not local" instruction:

- `geo.layers` holds **8** rows: `evacuation-zones`, `fire-detections`, `fire-perimeters`,
  `interventions`, `sensors`, `vegetation`, `water-gauges`, `weather-observations`.
  `Legend.tsx:16-23` maps only 6 — `evacuation-zones` and `sensors` have no toggle id.
  Fixtures use these real names; do not invent layer names.
- `geo.layers` has **none** of `temporal_kind` / `forecast_horizon_days` /
  `forecast_variants` / `earliest_observed_date`. Confirms §2: lane J writes that migration
  and lane G codes against the columns as if they exist.
- `to_regclass('geo.geometry')` and `to_regclass('geo.metric_daily')` are both **null** —
  lanes B and J have not landed. Confirms the lane must run entirely on a typed mock.
- **Trap 5 confirmed empirically:** every `geo.features.created_at` reads `2026-08-03`/`04`,
  including `fire-perimeters` rows that are certainly older. Slider depth from that column
  would render a one-day history. Depth comes only from `earliestObservedDate`.
- `agri.signal_observation` is **empty on prod** (the Boise depth is local-only), so there is
  no real series to read even if the read path existed.

### Verification policy for this lane

Per the owner: **no test→fix→test loop, and no per-agent sweeps.** Implementing agents run
no `npm test`, `npm run build`, `type-check` or `lint`. The orchestrator runs the §6 sweep
exactly once after every agent has reported, plus `npm run build`.

## 9. Execution record — 2026-08-03

Ran as a 6-agent workflow: recon (2) → contract/store (1) → component ‖ wiring (2) → review (1).
Implementing agents ran no verification; the orchestrator ran one sweep at the end.

### Delivered

| File | Change |
|---|---|
| `src/types/time-slider.ts` | net-new — the shared contract (§7 Q4 ruling) |
| `src/stores/time-slider-store.ts` | net-new — store + pure UTC selectors |
| `src/stores/useMetricAtDate.ts` | net-new — query hook, ±7-day prefetch, `NODE_ENV`-guarded mock |
| `src/components/map/TimeSlider.tsx` | net-new — integer-day slider, hatched future, variant toggle |
| `src/components/map/LayerManager.tsx` | edit — slider-day ref + four lane-J seams (§7 Q1 ruling) |
| `src/components/panels/WaterPanel.tsx` | edit — reads the selected day |
| `src/components/panels/VegetationPanel.tsx` | edit — reads the selected day |
| `src/__tests__/stores/time-slider-store.test.ts` | net-new — 23 tests |
| `src/__tests__/components/TimeSlider.test.tsx` | net-new — 9 tests |
| `src/components/ui/time-slider.tsx` | **deleted** (§7 Q3 ruling); zero importers re-confirmed |

### Sweep result

`type-check` 0 errors in lane G files · `lint` 0 problems in lane G files ·
`check:data-boundary` passed (12 documented URL rules) · lane G tests **32 new, all green**
(23 store + 9 component); no regressions in the 337-test suite.

Two failures during the sweep were traced to **other sessions**, not this lane:

- `src/__tests__/api/action-network.test.ts` failed once — vitest collected the file mid-write
  while another session lifted the `ACTION_NETWORK_INACTIVE` stub into a real implementation.
  Re-run: 3 tests pass. Not lane G's.
- **`npm run build` is currently red** and this lane cannot clear it. 23 `TS2339 … on type
  'never'` errors, **all 23 in `src/components/panels/AnalyticsDashboard.tsx`**, a file lane G
  never touched. Root cause is a concurrent edit to
  `src/lib/server/trpc/routers/analytics.ts` (not lane G's) that collapsed that query's
  inferred output type. `type-check` was clean at 22:38 and red at 22:50 with no lane G write
  in between. **Do not "fix" the consumer while the producer is mid-flight.**

### Post-review fix applied by the orchestrator

The reviewer escalated `selectedDateRef` as write-only: `LayerManager` subscribed reactively to
`selectedDate` but no render path read it, so once `TimeSlider` mounts every pointer tick of a
scrub would re-render ~8 layer children for nothing. Resolved by subscribing **outside React**
(`useTimeSliderStore.subscribe` into the ref) rather than by deleting the seam — the ref-
discipline rule lane J needs is preserved, and the per-tick re-render is gone. The four seam
comments now state that lane J needs a *reactive, debounced* day, since a ref cannot trigger a
refetch and the raw day would reintroduce the per-tick storm `useMetricAtDate` exists to bound.

### Lane status: built and tested, NOT yet reachable in the app

Both remaining steps are outside lane G's boundary and are **unowned**:

1. **Mount `<TimeSlider />` in `MapView.tsx`** as a sibling of `MapControls`. One line.
2. **Nothing calls `setCapabilities`**, so `capabilities` is permanently `null` — which means
   `TimeSlider` renders `null`, both panel date banners stay hidden, and every availability
   message is unreachable. This needs lane J's four `geo.layers` columns plus a tRPC procedure
   to serve `SliderCapabilities`. **Assign this before lane J starts** — the read path is
   specified here but owned by nobody.

Also still open: `Legend.tsx` should export `TOGGLE_ID_BY_LAYER_NAME` so the band-width key can
move into the legend proper (availability messaging is keyed on `geo.layers.name` meanwhile).
