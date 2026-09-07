---
type: evidence
---

# `reader_not_parquet` was stale for three layers, and one earlier blocker is refuted

Traced 2026-09-07 from the repository only — no database, no network, no Railway.

## The measured fact

After the coverage-authority flip, production `getSliderCapabilities` served 5 layers and withheld 19.
Three carried `reason: "reader_not_parquet"`: **sensors, watersheds, evacuation-zones**. That reason
is not about missing data — sensors held a published availability generation and was still withheld.

The cause is one declared field in `DIRECT_PARQUET_CAPABILITIES`
(`src/lib/server/services/parquet-slider-capabilities.ts:88-107`), turned into a withholding at
`:739`:

```ts
if (contract.servingReader !== "parquet") {
  return missing(contract, "reader_not_parquet", []);
}
```

The gate's stated purpose (`src/lib/server/AGENTS.md:1265-1267`) is to *"prevent a Parquet-derived
axis from driving a PostgreSQL-rendered population."* **For four of the five layers that named
`postgresql`, there is no PostgreSQL-rendered population left.** Wave C moved them to the Parquet
plane on 2026-09-04 and the contract line was never updated behind them.

## What `servingReader: "postgresql"` actually did

It did not serve a Postgres row. It **withheld everything**. `retainedPostgresCapabilities`
(`:242-253`) drops every layer in `PARQUET_CAPABILITY_NAMES` unless it is also in
`POSTGRES_CAPABILITY_PASSTHROUGH_NAMES` (`:177`, which holds only `burn-severity`) — so the Postgres
row was filtered out *and* the Parquet row refused, and those layers had **no slider row from any
source**. The Postgres query still ran on every call and its result for them was discarded.

## Flipped on 2026-09-07: sensors, watersheds, evacuation-zones

Three independent proofs that their render path does not touch Postgres:

1. Each tRPC procedure resolves to a Parquet reader — `getSensorStations` →
   `parquet-trpc-readers.ts:2200`, `getWatershedBoundaries` → `:2063`, `getEvacuationZones` →
   `:1923` — onto a GeoJSON source that `LayerManager.applyParquetFeatureData`
   (`LayerManager.tsx:794-808`) fills.
2. Martin publishes only `intervention_tiles` and `building_tiles`
   (`infra/martin/martin.yaml:65-71`), and `DYNAMIC_TILE_SOURCE_IDS = ["intervention_tiles"]`
   (`src/lib/map/sources.ts:59`). No tile function serves them.
3. **The production reason was itself the proof.** `reader_not_parquet` is the LAST check in
   `proveCapability`; a layer reaching it has already passed `availabilityWithholding` (`:803`),
   `lane_not_registered`, `lane_never_written`, `rung_not_reported`, `rung_never_written`,
   `lane_nature_mismatch`, `invalid_rung_bounds`, `ceiling_violation` and
   `no_common_readable_history`. All three therefore held a checksum-valid index with all four rungs
   reported. This also retires the evacuation-zones z9 doubt at `plan.md:235`: a missing rung would
   have surfaced as `rung_not_reported`.

**What the flip does not buy, stated plainly.** All three are `temporalKind: "snapshot"`, and
`sliderDomain` returns `null` for a snapshot (`src/stores/time-slider-store.ts:105`), so **none gains
a scrubber**. What changes: they stop being withheld, and `resolveLayerDate` starts using each lane's
own `latestObservedDate` instead of falling back to *today* — a real fix, since the map was asking the
Parquet plane for the wrong day.

## REFUTED: `geo.evacuation_zone_tiles` does not render anything

`evidence/removal-packet-watersheds-evacuation-zones-20260906.md:236-240` kept the evacuation-zones
Postgres producer alive on the grounds that *"`geo.evacuation_zone_tiles` still renders this layer as
a style-backed source … so stopping the Postgres producer freezes what the map draws."*

That misreads its own citation. In context (`src/components/map/AGENTS.md:285-291`, under the heading
*"Sensors and evacuation-zones: style-backed, and reading Parquet since 2026-09-04"*),
**"style-backed" means the style layer is declared in `styles.ts` rather than added by a React
component** — a statement about who owns the `addLayer` call, not about where the bytes come from.
The next line says so outright: *"`LayerManager` owns a fourth applier for these two … which writes
each read's collection onto the empty GeoJSON source `styles.ts` declares."*

Direct refutation: the function is unpublished in Martin and named by no style; the layer's fill and
outline bind `EVACUATION_ZONE_SOURCE = "evacuation-zone-features"` (`layers.ts:83`, `:256-259`).

**Consequence:** that blocker is void as argued. Stopping the producer freezes `geo.features` and the
census row built from it; it does not freeze one pixel. It may still be worth an owner decision, but
on D1 archive/parity grounds — not on a render dependency that no longer exists.

## Not flipped, and why

**burn-severity — rank 4 on effort, rank 1 on URGENCY.** It needs `:106` *and* removal from
`POSTGRES_CAPABILITY_PASSTHROUGH_NAMES` (`:177`, which returns early at `:805` so the line alone
changes nothing), plus a decision about `restoreCumulativeBurnHistory` (`:191-219`) which exists only
on the Postgres path. Without a replacement the synthesized axis would report inter-release days as
coverage gaps, while the reader walks back through releases and unions every one at or before the
requested day (`parquet-trpc-readers.ts:2162-2183`) — the axis and the reader would disagree.

Why it is nonetheless the most urgent: it is `temporalKind: "event"`, so it *does* get a real axis and
a style filter (`tile-layer-date-filter.ts:21`, `LayerManager.tsx:871`). **Today its scrubber is
driven by `geo.v_observation_day_census` while its pixels come from Parquet** — precisely the
inversion the gate exists to prevent, running unguarded in the other direction.

**soil-survey — not a contract question.** The lane has never written an object, so it is withheld as
`lane_never_written` and never reaches the reader gate. Its render path reads *and writes* Postgres:
`services/usda-soil.ts:534-535` (`geo.soil_survey_coverage`), `:994-998`, `:1084-1092`, `:1191-1199`
(`FROM geo.features`), and `:814-928` inserts into `geo.geometry`/`geo.features` on the SDA warm path.

## One defect outside the original scope, same criterion

`fire-perimeters` already declares `servingReader: "parquet"` and its map path is Parquet — but
`src/lib/server/services/regional-context.ts:502-528` still reads `geo.features` for it. That is a
live agent/regional-context Postgres read for an environmental layer, which criterion 2 names
explicitly (*"not the app, not the agent tools"*). It is the mirror image of the five contracts above
and is covered by no wave-C item.

## What is not established from the repo

- Whether the deployed Martin service carries the config that unpublishes the five tile functions.
  Settle with its `/catalog` endpoint.
- Whether the five `geo.*_tiles` SQL functions still exist in production.
  `drizzle/0039_drop_environmental_tile_functions.sql` drops all five and is deliberately
  **unjournalled** (`:1-4`, *"DORMANT … Firing it is wave D"*), so the repo records no production DDL
  state. Settle with `\df geo.*_tiles`.
- How many days the burn-severity axis would actually lose if the Parquet path synthesized it. That
  is the one number the burn-severity decision needs and it cannot be derived from the repo.
