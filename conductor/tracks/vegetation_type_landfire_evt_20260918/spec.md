---
type: Track Spec
title: Vegetation type — LANDFIRE LF2025 EVT as a governed static_lookup lane
description: Publish LANDFIRE Existing Vegetation Type for the PNW envelope (-125,42,-111,49) as the `vegetation-type` Parquet lane — a class-composition lattice at the coarse rungs plus the 30 m raster picture at the fine rungs — with the platform changes (per-rung key columns, latitude-band folding) it needs.
tags: [feature, vegetation_type_landfire_evt_20260918, pending]
timestamp: 2026-09-18
resource: ./metadata.json
---

# Track Spec: Vegetation type (LANDFIRE LF2025 EVT)

## Overview

Ship one new governed lane, slug `vegetation-type`, nature `static_lookup`, over the LANDFIRE
LF2025 Existing Vegetation Type raster for `PACIFIC_NORTHWEST_COVERAGE_BBOX = "-125,42,-111,49"`
(`services/agri-data-service/src/agri_data_service/ingest/policy.py:17`). The lane has two
artifacts, both required (owner D1, PLAN §8):

1. **The composition lattice** — the queryable truth. One row per `(cell, class)` carrying pixel
   counts on a regular degree lattice, base rung 0.005°, coarsened along the four-rung ladder
   `TIER_RESOLUTION_DEGREES = {9: 0.01, 5: 0.2, 0: 5.0}` (`warehouse/parquet/tiers.py:95`) with the
   class vocabulary coarsening in step: EVT code (1,069) at z13 → `EVT_GP` (192) at z9 →
   `EVT_PHYS` (20) at z5 → `EVT_LF` (10) at z0. The agent, the hover card and the slider capability
   read this plane.
2. **The raster picture** — a categorical PMTiles archive cut from the same `exportImage` windows,
   coloured with the legend's own `R,G,B`, published through `geo.raster_release` and read via
   `src/lib/server/services/raster-catalog.ts`. It is drawn where composition rows cannot be served
   (z9–z10 exceed `DAY_ROW_BUDGET` at any cap, ROW-CAP-ANALYSIS §3.4) and where the lattice is
   coarser than the eye (z11+). It is never the source of an answer the lattice cannot corroborate.

Two platform changes are prerequisites and are shipped and reviewed on their own before any lane
code depends on them (owner D3 revised 2026-09-18):

- **Per-rung key columns** on `GridAggregation` (`tiers.py:153-171`), because `key_columns` is one
  tuple for every rung today and the vocabulary ladder is otherwise not expressible.
- **Latitude-band folding** in `derive_and_write_day_tiers` (`pipeline/parquet/derivation.py:162-216`),
  keeping `MAX_DERIVATION_ROWS = 5_000_000` (`tiers.py:101`) as a per-call host guard rather than
  raising it. This is the step `pipeline/parquet/AGENTS.md:1058-1063` already names as missing.

This spec implements `.omc/research/runbook-20260915-vegetation-type/PLAN.md` (all sections, §8
decisions) and `ROW-CAP-ANALYSIS.md`. It does not re-litigate them. Where this document and those
disagree, those win and this document has a bug.

## Background

- Phase 0 of the PLAN is done: `src/lib/server/services/landfire.ts` was deleted and the About-page
  claim removed in `a17650b4`. `src/__tests__/app/about-page.test.tsx:45-48` now asserts LANDFIRE
  is named nowhere on the About page; Phase 4 of this track flips that assertion deliberately.
- Three lanes already run as `static_lookup` and are the precedents: `watersheds`
  (`pipeline/parquet/lane_registry.py:766-796`, package `pipeline/direct/watersheds/`),
  `evacuation-zones` (content-digest watermark, `pipeline/direct/evacuation_zones/watermark.py`),
  `soil-survey`. `foundation/parquet/lane_contract.py:153-201` enforces the nature: a watermark is
  mandatory, `forecast_module` and `writer_ceiling` are refused, `publication_lag_days` must be 0.
- The slider treats a static lane as `temporalKind: "snapshot"` through a capability row
  (`src/lib/server/services/parquet-slider-capabilities.ts:100-114`, e.g. line 109 for watersheds),
  not through `SNAPSHOT_SURFACE_LAYER_NAMES`. That resolves the PLAN's R12: route 1 with a
  catalogue row, and `sliderDomain` already excludes snapshot layers from the axis.
- Measured facts this spec relies on (ROW-CAP-ANALYSIS, eight real 60 km windows, 32 M pixels):
  6.25 EVT_NAME rows/cell at 0.005° (range 5.27–7.96) → ~22.8 M base rows (19.2–29.0 M);
  z9 (0.01°, `EVT_GP`) ~6.2 M rows; z5 ≤ 49 k; z0 ≤ 39. Arrow width 291 B/row with strings and
  WKB, 44–60 B/row numeric-only. Disk ~8 B/row zstd → ~179 MB per version at 0.005°.
  `exportImage` returns 2,000 px S16 TIFF windows in 2.3–11.1 s; the envelope is ~306 such windows.
- The SoilGrids "picture" the owner cited as the model is published (six COG + six PMTiles in
  `geo.published_raster`, `scripts/raster/AGENTS.md` §"Not done yet") but **not drawn**:
  `environmental.getPublishedSoilRasters` is `publicProcedure.query(() => [])`
  (`src/lib/server/trpc/routers/environmental.ts:583`) and `SoilLayer.tsx:86-87` returns before
  adding a source. Phase 3 therefore establishes the raster draw path rather than copying one.

## Functional Requirements

### FR-1 Lane declaration (layer-lane-standard §1, §2; layer-lanes §1a)
One `LaneRegistration` in `pipeline/parquet/lane_registry.py` for slug `vegetation-type`:
`nature="static_lookup"`, `publication_lag_days=0`, `watermark=` the LANDFIRE version resolver,
no `forecast_module`, no `writer_ceiling`, `floor_basis` quoting `docs/lanes/vegetation-type.md`
by section number. `HistoryCapability` is a typed refusal: LANDFIRE publishes discrete annual
releases and no continuous history.
- AC: `test_lane_contract.py`-style assertions prove all four `__post_init__` rules hold for the
  registration; a registration with `publication_lag_days=1` or a `forecast_module` raises.
- AC: `docs/lanes/vegetation-type.md` exists with §1 source, §2 cadence, §3 horizon, §4 grain,
  §5 known gaps, §6 validation, §7 `horizon: none` — the same seven sections `docs/lanes/watersheds.md` carries.
- Priority: P0.

### FR-2 Source watermark is a CHANGE event (layer-lanes §1a)
`pipeline/direct/vegetation_type/watermark.py` reads the LFPS folder listing
(`https://lfps.usgs.gov/arcgis/rest/services?f=json`) and names the newest `Landfire_LFxxxx` folder
containing `LFxxxx_EVT_CONUS` as the version. The watermark **day** is the release's dated
publication announcement, pinned as a cited constant per version; a version with no dated
announcement is a typed refusal (`watermark_unread`), never `now()` or the fetch clock
(layer-lane-standard §0 "never fabricate a date"). To cover the PLAN's R6 (silent republish in
place), the watermark basis also carries a digest of the ImageServer `f=json` metadata with
volatile fields removed; a digest change on the same folder name is a change event.
- AC: unit tests for the four outcomes — new folder, same folder same digest (sticky `current`),
  same folder new digest (`stale`), listing unreachable (`watermark_unread`) — against recorded
  fixtures; no network in tests.
- Priority: P0.

### FR-3 Base schema and grain (layer-lane-standard §4; ROW-CAP §4.1 pt 3, §4.2)
`warehouse/schemas/vegetation_type.py` registers stream `vegetation-type`, grain
`("cell_lon", "cell_lat", "evt_code")`, with these columns:

| column | type | null | aggregate at coarse rungs | note |
|---|---|---|---|---|
| `cell_lon`, `cell_lat` | float64 | no | re-floored | cell ORIGIN, floored (`tiers.py:161-165`) |
| `class_system` | string | no | `first` | `"LANDFIRE_EVT_LF2025"`; a future producer is distinguishable, never blended |
| `evt_code` | int32 | yes | key at z13; `null` above | the raster value; `-1` = `Other` (cutoff mass), `-9999` = NoData |
| `evt_group_code` | int32 | yes | key at z9; `first` at z13; `null` above z9 | legend `EVT_GP` |
| `evt_phys` | string | yes | key at z5; `first` below; `null` at z0 | legend `EVT_PHYS` (20 values) |
| `evt_lifeform` | string | no | key at z0; `first` below | legend `EVT_LF` (10 values); `"Fill-NoData"` for the NoData row, `"Other"` for the cutoff row |
| `pixel_count` | int64 | no | `sum` | 30 m pixels of this class inside the cell, counted in EPSG:5070 |
| `observed_at` | timestamp(us, UTC) | no | `first` | the LANDFIRE release date, not the fetch clock |
| `data_available_at` | timestamp(us, UTC) | no | `first` | publication; the leakage boundary |
| `release_day` | date32 | no | `first` | the version stamp = partition day = watermark day |

Not stored, on purpose: `evt_name` / `evt_group_name` (joined from the vendored legend at read
time — 291 → ~50 B/row in Arrow), `geom` (a cell polygon is `origin + rung resolution`), any
fraction (`area_fraction`, `nodata_fraction`), and `dominant_class` (an argmax that would drift).
NoData is a **class row** (`evt_code = -9999`) rather than a per-row `nodata_pixel_count` column,
because a per-row count summed across the class rows of one cell double-counts; the brief's intent
— counts, not fractions, so `sum` is the only aggregate — is met with one column. Fractions are
`pixel_count / sum(pixel_count) over the cell` computed by the reader, with and without NoData
stated separately.
- AC: a test proves `sum(pixel_count)` over all rows of a cell (including `Other` and NoData) equals
  the pixel count of that cell's footprint, on the eight retained windows.
- AC: a test proves the legend's functional dependencies hold (`EVT_GP → EVT_PHYS → EVT_LF`), which
  is what makes `first` a lawful aggregate for the dropped-key columns; the test reads the vendored CSV.
- AC: `TierDerivation.required_at_base` names `evt_code`, `evt_group_code`, `evt_phys` non-null at
  z13 even though the arrow schema permits null (`tiers.py:233-239`).
- Priority: P0.

### FR-4 Per-rung key columns on `GridAggregation` (platform change)
Add `key_columns_by_tier: Mapping[ZoomTier, tuple[str, ...]] | None = None` to `GridAggregation`
(`tiers.py:153-171`). `None` keeps today's behaviour for every existing lane byte-for-byte. When
set, `_derive_grid_tier` (`tiers.py:416-444`) uses the tier's tuple as the grain and requires that
every key column NOT in the tier's tuple has a `ColumnAggregation` of `first` or `null`. The
vocabulary ladder for this lane is `{13: ("evt_code",), 9: ("evt_group_code",), 5: ("evt_phys",),
0: ("evt_lifeform",)}`.
- AC: existing `tests/parquet/test_tiers.py` passes unchanged; a new test proves a lane with
  `key_columns_by_tier=None` derives identically before and after the change on a fixture.
- AC: a test proves a dropped key column with a `sum`/`mean` aggregate is refused with a message
  naming the column and the rung.
- AC: a test proves z0 derived from the z5 rung equals z0 derived from the base (associativity along
  the ladder) on the fixture.
- Priority: P0. Reviewed on its own before FR-6 lands.

### FR-5 Latitude-band folding in `derive_and_write_day_tiers` (platform change)
Add an optional band declaration beside `GridAggregation` (a new field on `TierDerivation`, e.g.
`band_height_degrees: float | None = None`). When set:
- band edges are multiples of 0.2° (the z5 pitch) so every z9 and z5 cell is complete within one
  band; **the invariant, stated:** flooring composes exactly only when a band edge is a multiple of
  both the base pitch and every derived pitch — 0.2 is a multiple of 0.005, 0.0025 and 0.01, and
  5.0 is a multiple of 0.2 (`tiers.py:95`); a band height that is not a multiple of 0.2 is refused;
- z9 and z5 are derived per band and each band's frame is asserted `≤ MAX_DERIVATION_ROWS`
  (`tiers.py:728-732` stays the guard; its comment at `tiers.py:97-100` is re-documented as "rows one
  `derive_tier` CALL may hold");
- **z0 is derived from the written z5 rung**, never from the base (≤ 49 k rows);
- `mean` and `sha256-lines` are refused in banded mode with a message naming the column
  (`pipeline/parquet/AGENTS.md:1062-1063`);
- the read-back selects only the base parts whose recorded `cell_lat` range intersects the band
  (`objectstore.py:808-846` gains a part filter; the base writer emits parts band-major, sorted by
  `cell_lat`, and records each part's `cell_lat` min/max in its receipt). Downloading a part outside
  the band is a test failure, not an inefficiency.
Every existing lane declares no band and keeps the whole-day path. Callers that reach this
function — `gap_fill.py:1204,1354,1541,1822,1973,2276`, `drain.py:659`,
`sensor_absence_correction.py:133` — are unchanged in signature.
- AC: on a synthetic 0.005° fixture spanning two bands, banded z9/z5/z0 equal whole-day z9/z5/z0
  exactly (row set and values).
- AC: a band edge at 42.1 (not a 0.2 multiple) is refused; a `mean` column in banded mode is refused.
- AC: a test proves the read-back for band k fetched no part whose `cell_lat` range lies outside k.
- AC: the `emptied`/`_retract_tier` semantics (`derivation.py:198-214`) hold per rung across bands —
  a rung is retracted only when EVERY band emptied it.
- Priority: P0. Reviewed on its own before FR-6 lands.

### FR-6 Windowed fetch, composition reducer, direct writer (layer-lanes §1, §4; standard §0)
`pipeline/direct/vegetation_type/` (files: `source.py`, `legend.py`, `reducer.py`, `rows.py`,
`watermark.py`, `adapter.py`, `forward.py`, `products.py`, `support.py`, `__main__.py`, and a
vendored `data/LF2025_EVT.csv` with its sha256 pinned in `legend.py`):
- `source.py` fetches `LF2025_EVT_CONUS/ImageServer/exportImage` windows ≤ 2,000 × 2,000 px with
  `bboxSR=imageSR=5070&format=tiff&pixelType=S16&noDataInterpretation=esriNoDataMatchAny&interpolation=RSP_NearestNeighbor&f=image`
  over a disjoint window tiling of the envelope's 5070 footprint (~306 windows). Any interpolating
  resample is refused by construction (the parameter is a `Final`, not an argument).
- `reducer.py` counts pixels per `(cell, evt_code)` in EPSG:5070 (each pixel 900 m²; PLAN R2 —
  counting in the equal-area frame, emitting 4326 cell origins from reprojected pixel centres),
  recognises NoData by legend non-membership (the TIFF carries no nodata tag, ROW-CAP §2.1), applies
  the declared 1 % cutoff into `evt_code = -1` (owner D7 stands), and is associative across windows
  so a cell straddling two windows is the sum of its two partial counts.
- Each finished window's partial counts are staged as one object under
  `layer=vegetation-type/staging/<version>/window-<n>.parquet` (precedent: `pipeline/direct/burn_severity/stage.py`)
  so a turn bounded by the executor timeout resumes where it stopped; the base is published only when
  every window is staged; staging is deleted after publication.
- `forward.py` publishes one snapshot at the watermark day, idempotent, dry-run by default,
  `--apply` to write; base parts at ~250,000 rows/part, band-major (ROW-CAP §3.2, `MAX_PART_INDEX`
  and the 1 MiB receipt).
- `pipeline/validation/vegetation_type.py` re-identifies N random land points against
  `.../ImageServer/identify` and asserts the returned class has `pixel_count > 0` in the written
  cell or falls in that cell's `Other` mass; mismatches name the point, the cell and the source response.
- AC: reducer tests on the eight retained windows reproduce `classes_per_cell.json` rows/cell within
  the tile range; a two-window fixture proves straddle-cell sums.
- AC: the writer refuses a window whose TIFF dimensions or pixel type differ from the request.
- AC: one coastal window (e.g. centre −124.6, 47.9) and one 49 °N window are exercised for NoData
  handling before the schema freeze (ROW-CAP §4.4 risk 6, §5).
- AC: `test_layer_import_contract.py` green — no cross-slug import, no write outside
  `layer=vegetation-type/`.
- Priority: P0.

### FR-7 Executor registration (standard §8 as corrected 2026-09-02, §13.1 step 4)
One `_spec(VEGETATION_TYPE_DIRECT_LANE_ID, command=("python", "-m",
"agri_data_service.pipeline.direct.vegetation_type"), ...)` in
`execution/job_executor_service.py` beside `job_executor_service.py:597-627` (watersheds), daily
cadence with its own phase offset, timeout sized for a bounded resumable turn, `selection_policy`
"one source-derived version per turn, published only when the LANDFIRE version watermark has moved".
The coverage-status duty is discharged by the generic static-lane census through `LANE_REGISTRY`
(`resolve_static_lane`, `lane_contract.py:272`); if that census does not already include every
`static_lookup`, the gap is a Phase 1 finding, not a second duty. The lane ships **shadow**: it is
added to `LANE_SPECS` and `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` is left untouched.
- AC: `tests/test_job_executor_service.py:38-41` (`EXPECTED_SCHEDULES`) is extended by hand with the
  new lane id and schedule; the test that no lane is active by default still passes.
- Priority: P0.

### FR-8 Serving reader (standard §9)
`planes/vegetation_type.py` (Python) and a TypeScript reader in
`src/lib/server/services/parquet-trpc-readers.ts` (the pattern at `:2187-2209` for watersheds)
exposed as `environmental.getVegetationType` in `src/lib/server/trpc/routers/environmental.ts`.
The reader answers one of three shapes and names which:
1. **composition** rows at the served rung when the viewport fits `DAY_ROW_BUDGET = 40_000`
   (`parquet_ops/serving.py:37`) and the 16 MiB client cap (`parquet-plane-client.ts:80`);
2. **dominant-class projection** (one row per cell) when composition does not fit but the
   projection does (ROW-CAP §3.4: z10 fits at 13.9 k rows);
3. a typed **`bbox_too_large_for_zoom`** refusal otherwise (z9 at 55.6 k projected rows), pointing the
   client at the raster.
Fractions are computed here from `pixel_count`; the legend names are joined here from the vendored
CSV; every response carries `class_system`, `release_day` and the vintage label.
- AC: tests cover each shape and the boundary between them with synthetic parts; no network.
- AC: the reader never reads `kind=forecast` (hardcoded `observed`, as `planes/watersheds.py:45`).
- Priority: P0.

### FR-9 Slider capability (standard §9.1, §10)
Add `{ layerName: "vegetation-type", temporalKind: "snapshot", parquetNature: "static_lookup",
servingReader: "parquet", parquetLanes: ["vegetation-type"] }` to
`parquet-slider-capabilities.ts:100-114`, and a registry entry with
`warehouseLayerName: "vegetation-type"` (`src/lib/map/layer-registry.ts`). The UI shows a **vintage
badge** ("LANDFIRE EVT, LF2025, published <date>") and mounts no axis; `forecastable: never`.
- AC: hand-spelled lists in `src/__tests__/services/parquet-slider-capabilities.test.ts` and
  `src/__tests__/lib/map/layer-registry.test.ts` are extended; neither imports the constant under test.
- Priority: P0.

### FR-10 Agent tools (standard §11)
`src/lib/server/services/vegetation-type-tools.ts` with two tools registered in
`regional-evidence-tools.ts` (catalogue cap `.max(32)` at `:30`; count current registrations first):
- `vegetation_type_at_point`: the composition of the cell containing the point at the UI-selected
  place, the vintage, and the K nearest cells with their real distance in km;
- `vegetation_type_in_area`: composition over a bbox at the served rung, with the shape returned.
Temporal proximity is a **typed refusal** ("one vintage published; no earlier or later
observation") until a second version exists. Both tools read the same reader as the map.
- AC: `src/__tests__/services/regional-evidence-tools.test.ts` extended; a test proves the refusal
  string is returned for any requested day.
- Priority: P1.

### FR-11 Raster rung (owner D1; ROW-CAP §4.2)
`scripts/raster/build-evt-mosaic.py` (assemble the staged/fetched S16 windows into one EPSG:5070
COG), `build-evt-tiles.py` (WarpedVRT to the XYZ pyramid with `Resampling.nearest`, **truecolour
RGB PNG** from the legend's `R,G,B` — a 256-entry palette cannot hold 1,069 classes, so the
paletted path of `build-soil-tiles.py` is not reused; z0–z14), `publish-evt-raster.py` (upload to
R2 under `raster/vegetation-type/…`, then register in `geo.raster_release` with a categorical
`color_ramp` and `unit = "evt_code"`). Served through `getPublishedRasters("vegetation-type")`
(`raster-catalog.ts:44`) via a new `environmental.getPublishedVegetationTypeRaster` procedure.
**Alternative stated:** if the raster cannot ship, the z9–z10 band is served as the dominant-class
projection of FR-8 and the map draws lattice cells there; z9 would then be a refusal and the toggle
would say so.
- AC: a verifier samples ≥ 200 land points via `identify` and asserts the tile pixel's colour maps
  back to the identified class; a wrong-CRS mosaic fails this.
- AC: the catalogue row's `bounds`, `minZoom`, `maxZoom`, `attribution` ("LANDFIRE, USGS/USFS,
  public domain"), `sourceRelease = "LF2025"` are asserted from the published row.
- Priority: P1 (blocks P0 for the wide zooms, per the analysis).

### FR-12 Map layer, legend, hover, panel IA, About (owner D6)
- `src/components/map/layers/VegetationTypeLayer.tsx`: the raster (`createPmtilesSource`,
  `src/lib/map/sources.ts:108-110`) at z ≥ 9; lattice cells filled by dominant class at z ≤ 8; hover
  and click read FR-8 at every zoom — the picture never answers, the lattice does.
- `src/lib/environmental/vegetation-type-legend.ts`: a generated `evt_code → {name, group, phys,
  lifeform, rgb}` table from the same vendored CSV, with a test that its sha256 matches the Python
  pin so the client legend cannot drift from the tiles.
- `src/lib/map/layer-legends.ts`: a spec whose colours are imported from the renderer module
  (rule 1 at `layer-legends.ts:8-11`); legend shows the 10 lifeform classes with the number of
  finer classes each summarises, and the hover card shows the composition.
- `src/components/panels/VegetationDetails.tsx`: three subsections under `panelId: "vegetation"` —
  **Vegetation Type** (vintage badge, resolution statement, composition at hover), **Greenness
  (NDVI)** (the existing slider controls), **Occurrences** (herbaria). No new `PanelId`
  (`layer-registry.ts:296-302`). One sentence relates type (baseline) to NDVI (state); no anomaly is computed.
- `src/app/about/page.tsx`: a LANDFIRE row — "Existing Vegetation Type, LF2025, USGS/USFS, public
  domain; composition at ~0.005° cells over WA/OR/ID with the 30 m picture at high zoom", note =
  the vintage. `about-page.test.tsx:45-48` is rewritten to assert the new claim by hand.
- Priority: P1.

## Non-Functional Requirements

### NFR-1 Executor memory (the risk, named)
Grid-lane derivation has no memory guard (`tiers.py:416-444`; `warehouse/parquet/AGENTS.md:130`);
the row cap is the only one. The executor container's memory limit is **unknown** — read it from
the Railway service before Phase 1 sizes bands, and size a band's Arrow frame to ≤ 1/3 of it. With
the FR-3 schema (~50–60 B/row Arrow) a 1.0° band at 0.005° is ~3.3 M rows ≈ 200 MB; that number is
asserted in a test against a synthetic band, not assumed.

### NFR-2 Serving budgets
`DAY_ROW_BUDGET = 40_000`, `WINDOW_ROW_BUDGET = 120_000` (`serving.py:37,43`), 16 MiB client cap,
`SERVING_MEMORY_LIMIT` × 3 readers. FR-8 shapes are chosen so no read exceeds them; a read that
would is a typed refusal. R2 GETs per read ≥ parts in the rung: base parts at ~250 k rows (~91
parts) and derived rungs at `DERIVED_ROWS_PER_PART = 10_000` (`derivation.py:101`, ~615 z9 parts —
a platform constant, changed only as its own decision).

### NFR-3 Honesty of the ~0.005° rung in the UI
The toggle label carries the class system and vintage ("Vegetation Type (LANDFIRE EVT 2025)").
Where lattice cells are drawn the panel states the cell size (~0.005° ≈ 420 m N–S) and that the
picture at higher zoom is the 30 m raster. The UI may not imply 30 m detail in the queryable plane.

### NFR-4 Publication volume and no local artifacts
~2.4 GB of S16 windows per version, streamed and reduced; nothing larger than one window is held.
Only staged Parquet, the published lane and the PMTiles archive land on R2 (memory: no large local
artifacts). One version ≈ 230 MB Parquet + the archive.

### NFR-5 Security / licence
No credentials: LFPS is anonymous. LANDFIRE is US federal public domain; the exact attribution
string is confirmed against landfire.gov terms before Phase 3 publishes it (PLAN R5;
`copyrightText` is empty on the service).

## User Stories

- As a wildfire prevention planner, I want to hover a place and see "62 % Douglas-fir–Western
  Hemlock Forest, 21 % Developed-Low Intensity (LANDFIRE EVT 2025)", so I know the vegetation type
  behind the fuel and greenness layers.
  Given the lane is published, when I hover at z11 in Washington, then the card shows the cell's
  composition with fractions, the vintage badge, and the cell size.
- As an analyst, I want to zoom to z13 and see the 30 m class picture, so I can read stand
  boundaries.
  Given the raster archive is catalogued, when I zoom past z9, then the PMTiles raster draws and the
  hover still answers from the lattice.
- As the agent, I want to ask for the vegetation type at the selected place, so I answer with the
  same data the map shows.
  Given a point, when `vegetation_type_at_point` runs, then it returns composition, vintage, and
  nearest cells with km distances; when a day is requested, it returns the single-vintage refusal.
- As the operator, I want a new LANDFIRE release to be detected without re-publishing on every tick.
  Given LF2026 appears in the folder listing, when the executor lane runs, then `resolve_static_lane`
  reports `stale` and one snapshot is published under the new watermark day; given nothing changed,
  it reports `current` and writes nothing.

## Technical Considerations

- **Ordering.** FR-4 and FR-5 are platform changes; each is its own push and its own adversarial
  review before FR-6 registers a derivation that needs them (owner: push small and often).
- **Base rung sequencing.** 0.005° first. 0.0025° only after banding is proven on 0.005° in
  production (18 bands of 0.4°, ~3.7 M rows each) — a follow-up decision, not this track.
- **Cutoff semantics.** The 1 % rule is near-inert at 0.005° (ROW-CAP §2.2) and does not bound
  rows/cell; it is kept because it is the declared owner rule (D7). The reducer reports the dropped
  mass per window in its receipt.
- **`first` as an aggregate for dropped keys** is lawful only because the legend nests
  functionally (FR-3 AC). If a future legend breaks the nesting, the test fails before the schema does.
- **Raster catalogue in Postgres.** `geo.raster_release` exists in `drizzle/0000_baseline.sql`.
  `conductor/tracks/environmental_parquet_serving_20260912/spec.md:13` permits "small reference
  lookups explicitly outside the environmental payload plane"; a release row is one. The soil stub
  at `environmental.ts:583` is not a precedent to copy but a question to answer (Open Question 3).
- **Playwright evidence** uses a standalone config under this track's `evidence/` directory
  targeting the deployed app; the repo's `playwright.config.ts` boots the app locally and is never
  used (memory: never run PlantGeo locally).
- **Concurrent RUNBOOK writers.** Pick a § number at write time; never `git add conductor/` wholesale.

## What this lane does NOT claim

- **No time series.** One version stamp per LANDFIRE release; the slider shows a vintage badge, not
  an axis. There is no "vegetation type on 2024-06-01".
- **Not a fuel model.** FBFM40 is a separate LANDFIRE product (`LF2025_FBFM40_CONUS`, value space
  91–204) and is not chartered here (owner D5). The legend's `EVT_FUEL` crosswalk is vendored but not
  served.
- **Not 30 m in the queryable plane.** Composition cells are ~0.005°; the 30 m detail is a picture.
- **Not British Columbia.** The envelope stops at 49 °N; BC BEC is a separate future producer.
- **Not a type-conditioned NDVI anomaly.** Type and greenness are co-located, not combined.
- **Not a forecast.** `forecastable: never`; no `method/monte_carlo/vegetation_type.py` exists.

## Out of Scope

BC/BEC; FBFM40; a 0.0025° base rung; changing `DERIVED_ROWS_PER_PART`; a new `PanelId`; fixing
the SoilGrids draw path (a sibling finding, recorded, not fixed here); herbaria changes.

## Open Questions (carried as explicit checks; ROW-CAP §5)

1. **Executor memory ceiling** — read from Railway before band sizing. Default until read: assume
   2 GB and bands of 1.0°; reversal cost low (a constant).
2. **True land-cell fraction** (93 % guessed) and the envelope-wide rows/cell tail — measured by the
   first full staging pass; the base part count and receipt size are asserted after it, not before.
3. **Raster catalogue location** — `geo.raster_release` (default, per the brief) vs an object-store
   manifest. Ask before Phase 3 publishes. Reversal cost: one publish script and one reader.
4. **NoData at the coast and at 49 °N** — sentinel value vs 0 for out-of-footprint pixels. Checked by
   the two windows in FR-6 before the schema freeze.
5. **`exportImage` throttling across ~306 sequential requests** — the staged, resumable fetch makes
   this a wall-time question, not a correctness one; the first full pass records it.
6. **LF2025 dated publication announcement** for `observed_at`/the watermark day — looked up and
   cited, or the version is refused (FR-2).
7. **Whether LANDFIRE republishes an LFxxxx EVT in place** — mitigated by the metadata digest in
   FR-2; confirmed only by watching LF2026 land.
8. **Agent tool catalogue headroom** under `.max(32)` — counted in Phase 2 before adding two.
