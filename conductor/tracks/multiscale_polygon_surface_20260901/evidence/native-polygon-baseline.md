---
type: track-evidence
slug: multiscale_polygon_surface_20260901
artifact: native-polygon-baseline
status: authored
---

# m4 — native polygon baseline

What the six `native_polygon` products are made of, what generalises them, where their identity
lives, and what a production camera still has to prove. Written by the implementing slice; no
test, lint or build was run in this lane (`plantgeo-authoring-and-verification-are-separate-agents`),
so every claim below is marked **code-verified** (readable in the tree) or **needs production
pixels**.

Read against HEAD `9052998` plus the wave-2 working tree, so line citations into
`src/lib/map/layer-render-contract.ts` and `src/lib/environmental/parquet-presentation.ts` are
against the uncommitted state those concurrent slices left; the facts they cite were re-checked
after the last edit to either file.

The regression that pins the code-verified half is
`src/__tests__/components/native-polygon-regression.test.tsx`.

## The claim

Spec render table, row 3: `native_polygon` products accept "topology-preserving simplify/dissolve"
at coarse and middle, and "source geometry" at detail. `src/lib/map/layer-render-contract.ts:369-380`
implements that as one permitted form in all three bands:

```ts
function nativePolygonEntry(layerId: LayerToggleId): LayerRenderContractEntry {
  return {
    layerId,
    renderClass: "native_polygon",
    permittedForms: {
      coarse: NATIVE_POLYGON_FORMS,
      middle: NATIVE_POLYGON_FORMS,
      detail: NATIVE_POLYGON_FORMS,
    },
    declaredSupportDegrees: null,
  };
}
```

`native_polygon` is not "the shape at full resolution" — it is "the shape the source published,
generalised only along its own boundary". A simplify or a dissolve stays inside the form; a cell,
a centroid dot, a cluster or a smoothed surface leaves it. `declaredSupportDegrees` is null for all
six because source geometry has no cell size to declare.

## Per-layer table

| layer | source of drawn geometry | drawn form | generalisation, and where it happens | identity key |
|---|---|---|---|---|
| `burn-severity` (MTBS) | Martin `burn_severity_tiles` → `geo.features`, layer `burn-severity` | `fill` + `line` | **none at any zoom** — `ST_AsMVTGeom(ST_Transform(f.geom,3857), bounds, 4096, 64, true)` and nothing else | `f.id` as an MVT *attribute*; `fireId`/`fireName` also travel |
| `fire-perimeters` | Martin `fire_risk_tiles` → `geo.features`, layer `fire-perimeters` | `fill` + `line` | none in the tile; the *metric* read path simplifies (see below) | `f.id` attribute; `severity` derived at ingest |
| `evacuation-zones` | Martin `evacuation_zone_tiles` → `geo.features` | `fill` + `line` | none | `f.id` attribute; `severity` from `evacuationLevel` |
| `watersheds` | Martin `watershed_tiles` → `geo.features` (z≥10) or `geo.watershed_rollup` (below) | `fill` + `line` | **hierarchical dissolve by HUC prefix, then `ST_SimplifyPreserveTopology`**, in the matview | `huc12` at detail; `huc` + `huc_level` on a rolled-up basin |
| `drought` (USDM) | Parquet lane `drought`, read by `getParquetDrought`, drawn by `DroughtLayer` | `fill` + `line` | `ST_SimplifyPreserveTopology` in DuckDB at export, one tolerance per rung | `area_id` → GeoJSON `Feature.id` |
| `soil-survey` (SSURGO) | tRPC `environmental.getSoilSurvey` → PostGIS, drawn by `SoilSurveyLayer` | `fill` + `line`, **plus a `circle` summary tier** | three answers by viewport area: raw delineations / dissolve-by-drainage-class + simplify / counted point lattice | `mupolygonkey` at detail; none on the two coarser tiers |

## Layer by layer

### `burn-severity` — the continuity reference. Code-verified.

`drizzle/0038_tile_low_zoom_routing.sql:484-536`. The whole geometry expression is:

```sql
ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
```

Transform, clip to the tile envelope with a 64-unit MVT buffer, encode. There is no
`ST_Simplify*`, no `ST_Union`, no `ST_Buffer` and no zoom branch anywhere in the function, at any
`z`. That is exactly why the 2026-09-01 assessment saw coherent polygons here and blocks elsewhere:
MTBS is the layer that never had a re-derivation step to get wrong.

The cost is real and recorded in the same migration's header: 541 published rows carrying
**2,341,323 vertices / 37.5 MB, cold-read 28.4 s**. 0038 deliberately did *not* add simplification —
`LIMIT 10000` is a row ceiling that is unreachable at 541 rows and changes the emitted tile by
nothing. So the reference layer is also the most expensive one, and the m5 feature/byte budget gate
has to be measured against it rather than assumed.

Style: `burnSeverityLayer` / `burnSeverityOutlineLayer` (`src/lib/map/layers.ts:308-340`), `fill`
keyed on `acres` with a log-spaced ramp, `line` at width 1. Both bind source `burn_severity_tiles`,
source-layer `burn_severity`. On the in-place date filter
(`DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS`), so scrubbing re-filters tiles already in the browser and
never fetches different geometry for a different day.

### `fire-perimeters` — code-verified, with one caveat off the map path

Tile path identical in shape to MTBS: `drizzle/0038_tile_low_zoom_routing.sql:441-478`, 177
published rows, no simplification.

Caveat, and it is not the map: `getMetricAtDate` in
`src/lib/server/services/environmental-read-model.ts:3809-3812` *does* generalise polygon layers —

```ts
const geometrySql = sql`CASE
  WHEN g.geom_kind = 'point' THEN g.geom
  ELSE ST_SimplifyPreserveTopology(g.geom, ${tolerance})
END`;
```

with `tolerance = droughtSimplifyTolerance(bboxWidth)` = `min(0.05, max(0.0005, width/2000))`, on
the stated grounds that a perimeter reaches ~58,000 vertices. That path feeds panels and the
metric-at-date API, not the drawn tile layer, so the two disagree on vertex count for the same
feature. Topology-preserving and therefore inside the contract; worth naming so a reader comparing
a panel readout to the map does not think one of them is wrong.

### `evacuation-zones` — code-verified

`drizzle/0038_tile_low_zoom_routing.sql:540-582`, 651 rows, no simplification. `fill` at opacity
0.45 keyed on a three-level `severity` derived at ingest, plus a 1.5px outline.

### `watersheds` — code-verified; the pattern the other tile layers owe

The one layer whose coarse rung is a *real parent feature* rather than a generalised child.
`drizzle/0023_watershed_zoom_generalization.sql:21-93` builds `geo.watershed_rollup` by truncating
the HUC12 code, level by level, simplifying between stages:

| rollup level | grouped by | tolerance (degrees) |
|---|---|---|
| HUC10 | `left(huc,10)` | 0.0015 |
| HUC8 | `left(huc,8)` | 0.005 |
| HUC6 | `left(huc,6)` | 0.015 |
| HUC4 | `left(huc,4)` | 0.04 |

Each stage is `ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(geom), <tolerance>))` over the
level below, so a HUC6 polygon is exactly the union of its member HUC12s — a published USGS
classification, not an invented grouping. The tile function (newest definition
`drizzle/0033_tile_function_partition_pruning.sql:476+`) picks the rung by zoom: `z≥10 → 12`,
`z≥8 → 10`, `z≥6 → 8`, `z≥4 → 6`, else 4.

Identity survives honestly in both directions: a rolled-up basin carries `huc` + `huc_level` and
NULLs `huc12`, `name`, `tohuc`, `states`, `hutype`, because a member's value would be a lie about
the whole. That is the `aggregationMethod: "dissolve"` case of the serving contract done right.

Payload evidence for why the rollup exists, measured against production Martin 2026-08-08 at
(-122, 45.5) with the raw 9,396-basin set: z4/2/5 = 2.54 MB, z5/5/11 = 3.80 MB, ~7.6 MB per default
viewport. The remedy was cartographic, not a `minzoom` floor — and `watersheds-fill` is
consequently the only native fill in the tree with **no** minzoom.

### `drought` — code-verified through the Parquet lane

`LayerManager` calls `environmental.getDroughtClassification`, which is
`getParquetDrought` (`src/lib/server/services/parquet-trpc-readers.ts:1547+`). The rung is chosen
client-zoom-first — `const zoomTier = resolveZoomTier(input.mapZoom)` — and the lane's `geom` is
decoded by `decodeDroughtGeometry`, which accepts **only** `Polygon` or `MultiPolygon` and throws a
contract error otherwise. `presentParquetDrought`
(`src/lib/environmental/parquet-presentation.ts:219-239`) copies `area.geometry` through untouched
and sets `id: area.areaId`; `DroughtLayer` adds a `fill` and a `line` over that collection with no
transform of any kind.

Warehouse-side generalisation, `services/agri-data-service/src/agri_data_service/warehouse/schemas/drought.py:95-101`
plus `warehouse/parquet/tiers.py:94,576-645`:

| rung | `ST_SimplifyPreserveTopology` tolerance | dissolve | area floor |
|---|---|---|---|
| z13 (base) | none — the exporter's own grain | — | — |
| z9 | 0.01° | none | none |
| z5 | 0.2° | none | none |
| z0 | 5.0° | none | none |

`min_area_tier_squares` is deliberately `None` on all five simplify-only lanes: the first choice of
`1.0` would drop every feature smaller than one z0 grid square (25 sq deg) and the entire PNW
universe is ~10×10 degrees, so it emptied the lane at z0. Recorded in each schema's header.

The older PostGIS drought reader (`readDroughtRelease`, `environmental-read-model.ts:890,916`) is
still present and uses the viewport-derived tolerance instead; it is not what the map draws.

### `soil-survey` — code-verified, and it is the one that breaks form

Three server answers, chosen by measured viewport **area** rather than zoom tier
(`src/lib/server/services/usda-soil.ts:379-388`):

| branch | when | geometry | identity |
|---|---|---|---|
| `readDetailFeatures` | granularity `detail` (z ≥ `SOIL_SURVEY_DETAIL_MIN_ZOOM`) | raw SSURGO delineations | `mupolygonkey` as `Feature.id` |
| `readAggregatedFeatures` | area ≤ `MAX_SOIL_UNION_SQUARE_DEGREES` (~0.48 sq deg, ≈ z10.6) | `ST_Union` per drainage class + `ST_SimplifyPreserveTopology` at 0.0015° (regional) / 0.005° (coarse) | none — `aggregated: true` + `drainageClass` + `mapUnitCount` |
| `readSummaryFeatures` | anything wider | **`Point` at each lattice cell's centre** | none — cell (col,row) only |

The first two are inside the contract: a dissolve by drainage class is a declared dissolve, and the
class table it dissolves on is the same one the detail fill matches, so a colour never changes
meaning across the tier boundary. `mukey` is *not* the identity — measured over one Boise cell, 683
delineations collapsed to 98 distinct mukeys, so `mupolygonkey` is used instead.

The third is not. `soilSurveySummaryLayer` (`src/lib/map/layers.ts:606-628`) is `type: "circle"`,
filtered to `["==", ["get","summary"], true]`, radius interpolated on `mapUnitCount` (1→4px,
250→8px, 2 000→13px, 20 000→19px). On this track's vocabulary that is `raw_point`/`aggregate_cell`
territory, and the contract permits neither. See "Recorded gaps".

## Client-side geometry work: none. Code-verified.

Grep over every module between the wire and the canvas — `DroughtLayer.tsx`, `SoilSurveyLayer.tsx`,
`lib/map/layers.ts`, `lib/map/layer-utils.ts`, `lib/environmental/parquet-presentation.ts` — finds
no `@turf/*` import and no call-shaped `simplify(`, `buffer(`, `dissolve(`, `convexHull(`,
`concaveHull(`, `polygonSmooth(` or `cleanCoords(`. The only `interpolate` in `layers.ts` is a
**colour** ramp, never a geometry one. Both component layers hand MapLibre the exact
`FeatureCollection` object they received; the regression asserts that by reference, which a
renderer that re-derived anything could not satisfy.

There is also no bare `ST_Simplify(` anywhere in `drizzle/`. Every generalisation in the tree is
`ST_SimplifyPreserveTopology`, which is the spec's word and not decoration: plain `ST_Simplify` may
return a self-intersecting or empty ring, which draws as a bow tie *and* answers point-in-polygon
wrongly — and `planes/drought.py` runs exactly that point-in-polygon test against this geometry.

## Recorded gaps — reported, not fixed by m4

### 1. Three fire-family fills vanish below zoom 4, inside the coarse band

`firePerimetersLayer`, `evacuationZonesLayer` and `burnSeverityLayer` all carry `minzoom: 4`
(`src/lib/map/layers.ts:170`, `:250`, `:308`). The coarse band spans z0 to just under z9
(`ZOOM_TIER_BANDS`), so z0–z3.99 draws nothing for the three. A layer that vanishes when you zoom
out is indistinguishable from a layer with no data — the exact confusion 0023 removed for
watersheds by drawing a coarser rung instead of hiding the layer, and the fix these three still owe.
Not on the default camera's path (z≈5.9), so it is a correctness gap rather than a live outage.
**Owner: m5 / a renderer slice. Needs production pixels to price** (what a z2 MTBS tile actually
weighs unsimplified).

### 2. `soil-survey` draws circles at the default camera

At the default PNW camera the viewport is ~98 square degrees against a 0.48 sq deg union budget,
so `readSummaryFeatures` answers and the map paints an 8×8 lattice of count-scaled dots for a
product the contract says is `native_polygon` at every band.

This is the same class of gap `vegetation` carried as a recorded `shippedDeviation`
(`layer-render-contract.ts:445-453`) — and note that `vegetation`'s was *closed* by slice m3 in
this same wave, which leaves `soil-survey` as the only native-class layer whose renderer and
contract disagree with **nothing recorded anywhere saying so**. Read the contract today and the
soil survey looks compliant.

m4 deliberately does not widen the contract to legalise it: a dot whose radius means "how many
delineations were counted here" is not the survey's geometry, and permitting it would delete the
only record that the two disagree. **Proposed handoff: record a `shippedDeviation` on
`soil-survey` naming form `aggregate_cell`, owner and date** — that file is W2-C's write set this
wave, so m4 reports it rather than editing it.

## What production must still show — needs production pixels

MTBS is the continuity reference, so the acceptance camera set is anchored on it. Default PNW
camera is derived, not hand-picked: `FALLBACK_COVERAGE_BBOX` (-125, 42, -111, 49) fitted to
1024×512 with 40px padding gives centre **(-118, 45.5), zoom ≈ 5.92** (`src/stores/map-store.ts:59-80`,
`src/lib/map/coverage-region.ts:120-148`) — which resolves to the **coarse** band.

| # | camera | layers on | what the screenshot must show |
|---|---|---|---|
| S1 | default PNW, z≈5.9 (coarse) | `burn-severity` only | Scar boundaries closed and coherent; no seams, no dots. The reference frame every other layer is compared to. |
| S2 | same camera | `burn-severity` + `fire` | Detection cells visually and textually distinct from scars — spec gate "event aggregates are visually and textually distinct from native perimeters". |
| S3 | z9.5 over the Oregon Cascades (≈ -122.2, 44.8) (middle) | `burn-severity` | Same scars, more vertices, no popping or re-registration versus S1. |
| S4 | z14 over one scar (detail) | `burn-severity` | Source geometry; outline reads as a boundary, not a smudge. |
| S5 | S1/S3/S4 cameras | `watersheds` | HUC4→HUC12 rungs each drawn as one basin set; exactly one rung visible per camera, no overlap at a rung boundary. |
| S6 | S1/S3/S4 cameras | `drought` | Class boundaries closed at every rung; no background visible through cracks between D-classes. |
| S7 | S1 and S4 | `soil-survey` | Records gap 2: dots at S1, polygons at S4. Capture before any fix so the change is provable. |
| S8 | z2 (coarse) | `burn-severity`, `fire-perimeters`, `evacuation-zones` | Records gap 1: all three absent. Plus a tile-byte measurement, which is the number that decides whether the fix is a rung or a floor. |

Also needed from production and not derivable here: feature count, bytes and request-to-paint per
tile at S1 for the four Martin layers (the m5 budget gate), and confirmation that the drought lane
has z0/z5/z9 rungs published for the day under test — the client resolves the rung before it knows
whether one exists.

## Not covered by this slice

Legend/caption wording, hover payloads (`burn-severity` and `drought-fill` are absent from
`HOVERABLE_LAYER_IDS`, so MTBS identity never reaches a reader's tooltip at all — a real gap, but a
caption gap, not a geometry one), and the `fire` event lane, which is m3's.
