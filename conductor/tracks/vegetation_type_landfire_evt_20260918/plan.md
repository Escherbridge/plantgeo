---
type: Implementation Plan
title: Vegetation type — LANDFIRE LF2025 EVT as a governed static_lookup lane
tags: [vegetation_type_landfire_evt_20260918]
resource: ./spec.md
---

# Implementation Plan: Vegetation type (LANDFIRE LF2025 EVT)

## Overview

Four phases, each independently shippable, each ending in one integrated sweep and one independent
adversarial review. Authors never run the suite (owner 2026-08-25); they predict what will fail and
the monitor sweeps the combined tree once per phase. Every task is one TDD cycle: write the test,
watch it fail, implement, refactor. `path:line` citations are the touchpoints as of HEAD
`ec172e88`; re-verify with a grep before editing, since HEAD moves.

| phase | ships | review lane |
|---|---|---|
| 1 | per-rung keys (platform), banded derivation (platform), the lane writer + executor registration (shadow) | `/code-review high` on each platform change separately, then on the lane; `oh-my-claudecode:critic` on the banding invariant |
| 2 | Python plane + TS reader, slider capability, agent tools, static coverage semantics | `/code-review high`; `/security-review` on the two agent tools (user input) |
| 3 | raster mosaic → truecolour tiles → PMTiles → catalogue row → tRPC read | `/code-review`; verifier re-runs the identify-sample check |
| 4 | map layer, legend, hover, panel IA, About row, live browser evidence | `/code-review`; `designer` pass on the panel IA; `conductor-okf:code-styleguides` loaded for every review |

Source of truth for decisions: `.omc/research/runbook-20260915-vegetation-type/PLAN.md` §8 and
`ROW-CAP-ANALYSIS.md` §4. Nothing below reopens them.

---

## Phase 1: Schema first, then banding, then the lane

Goal: the platform can express the vocabulary ladder and derive a 23 M-row base without holding
it; the `vegetation-type` lane writes one governed snapshot and is registered (shadow) in the
executor. Three pushes minimum: 1A, 1B, then 1C+1D.

### 1A — Per-rung key columns (`GridAggregation`) — platform, reviewed alone

- [ ] Task: Pin today's behaviour. Add `tests/parquet/test_grid_per_tier_keys.py` with a fixture
      lane using `key_columns` only; assert z9/z5/z0 output is byte-identical before and after the
      change (golden frame). (TDD: write test, implement, refactor)
- [ ] Task: Add `key_columns_by_tier: Mapping[ZoomTier, tuple[str, ...]] | None = None` to
      `GridAggregation` at `services/agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py:153-171`;
      docstring states the `first`/`null` rule for dropped keys, terse; rationale goes to
      `warehouse/parquet/AGENTS.md`.
- [ ] Task: Teach `_derive_grid_tier` (`tiers.py:416-444`) to resolve the tier's key tuple; refuse a
      dropped key column whose `ColumnAggregation.how` is not `first`/`null`, naming column and rung
      (`TierDerivationError`). Test: `sum` on a dropped key raises with both names in the message.
- [ ] Task: Extend `register_tier_derivation`'s conflict/validation path (`tiers.py:700-780`,
      `named = {...}` at `:777`) so per-tier keys are included in the "every named column exists in
      the schema" check. Test: a tier tuple naming an unknown column is refused at registration.
- [ ] Task: Associativity test — z0 from z5 equals z0 from base on a fixture with a three-level
      functional hierarchy; documents why `first` is lawful.
- [ ] Task: Re-document `MAX_DERIVATION_ROWS` at `tiers.py:97-101` as "rows one `derive_tier` CALL
      may hold" (comment only; ROW-CAP §4.1 pt 1) and update `pipeline/parquet/AGENTS.md:1058-1063`
      to point at 1B.
- [ ] Verification: monitor sweep (`uv run --frozen pytest tests/parquet`, `mypy --strict`, ruff
      deferred per owner); independent `/code-review high` verdict recorded in
      `conductor/tracks/vegetation_type_landfire_evt_20260918/evidence/review-1A.md` [checkpoint marker]

### 1B — Latitude-band folding in `derive_and_write_day_tiers` — platform, reviewed alone

- [ ] Task: Band invariant test first. In `tests/parquet/test_banded_derivation.py`: a synthetic
      0.005° `GridAggregation` lane spanning 42.0–44.0 with `sum` columns; banded (1.0°) z9/z5/z0
      equal whole-day z9/z5/z0 exactly. Second test: band height 0.3 (not a 0.2 multiple) refused.
      Third: a `mean` or `sha256-lines` column in banded mode refused, message names the column.
- [ ] Task: Add `band_height_degrees: float | None = None` to `TierDerivation`
      (`tiers.py:227-240`); `__post_init__` refuses a non-multiple of 0.2 and refuses it on any
      non-`GridAggregation` strategy. The refusal message states the invariant: "band edges must be
      multiples of every rung pitch so flooring composes exactly".
- [ ] Task: Part receipts carry lat bounds. Extend the base-write receipt in
      `pipeline/parquet/objectstore.py` (the `ReadPartReceipt`/write receipt neighbourhood,
      `:808-846`) with optional `latitude_min`/`latitude_max`; the writer path records them when the
      lane declares a band. Test: a part written without a band has `None`; with a band, the bounds
      match the frame.
- [ ] Task: Band-scoped read-back. Add a part filter to `read_partition_with_receipts`
      (`objectstore.py:808-846`) taking a latitude interval; only parts whose recorded bounds
      intersect are fetched. Test with a fake backend that records `get` calls: band k fetches no
      out-of-band part; a part with unknown bounds (legacy) IS fetched (fail-open on unknown, so
      existing lanes are never silently truncated).
- [ ] Task: Fold in `derive_and_write_day_tiers` (`pipeline/parquet/derivation.py:162-216`): when
      the registered derivation declares a band, iterate bands over the day's latitude extent, derive
      z9 and z5 per band asserting `≤ MAX_DERIVATION_ROWS` per band, accumulate per-rung parts
      band-major, then derive z0 from the accumulated z5 frame. Keep the all-or-nothing contract:
      any band failure raises `TierWriteError` and nothing is marked. `base_table` callers
      (`gap_fill.py:2032,2134,2146`) pass through unchanged — if `base_table` is given with a banded
      lane, band by filtering the frame, never by re-reading.
- [ ] Task: `emptied`/retraction semantics across bands (`derivation.py:198-214`): a rung is
      retracted only when every band produced zero rows. Test: one empty band, one non-empty band →
      rung written, not retracted.
- [ ] Task: Signature stability test — every caller listed in the spec (`gap_fill.py:1204,1354,1541,
      1822,1973,2276`, `drain.py:659`, `sensor_absence_correction.py:133`) still type-checks with no
      edits; assert via the existing `tests/parquet/test_derivation_and_drain.py` and
      `tests/parquet/test_gap_fill.py` passing untouched.
- [ ] Task: Document the design in `pipeline/parquet/AGENTS.md` (replace the "needs this function
      taught to fold" paragraph at `:1058-1063` with the banded contract and its invariant).
- [ ] Verification: monitor sweep of `tests/parquet`; `oh-my-claudecode:critic` asked to refute the
      exactness claim (flooring composition, z0-from-z5); `/code-review high`; verdict in
      `evidence/review-1B.md` [checkpoint marker]

### 1C — The lane: legend, fetch, reducer, writer, validation

- [ ] Task: Write `docs/lanes/vegetation-type.md` first (seven sections as `docs/lanes/watersheds.md:20-385`),
      so `floor_basis` can cite it. State the ~0.005° rung, the 1 % cutoff, NoData-as-a-class-row,
      `horizon: none`.
- [ ] Task: Vendor `LF2025_EVT.csv` (from `.omc/research/runbook-20260915-vegetation-type/LF2025_EVT.csv`,
      309,948 bytes, 1,069 rows) at `pipeline/direct/vegetation_type/data/LF2025_EVT.csv`; `legend.py`
      pins its sha256 and parses `VALUE, EVT_NAME, EVT_GP, EVT_GP_N, EVT_PHYS, EVT_LF, R, G, B`.
      Tests: checksum mismatch refuses; `EVT_GP → EVT_PHYS → EVT_LF` is functional (the `first`
      lawfulness test, FR-3); NoData/`Other` sentinels are not legend members.
- [ ] Task: `warehouse/schemas/vegetation_type.py` — `VEGETATION_TYPE_STREAM`, grain
      `("cell_lon","cell_lat","evt_code")`, arrow schema per spec FR-3, `register_tier_derivation`
      with `GridAggregation(key_columns=("evt_code",), key_columns_by_tier={13: ("evt_code",),
      9: ("evt_group_code",), 5: ("evt_phys",), 0: ("evt_lifeform",)}, aggregations=(sum pixel_count,
      first/null per spec))`, `band_height_degrees=1.0`, `required_at_base=("evt_code",
      "evt_group_code","evt_phys")`. Test: schema conformance of a reducer output frame; a frame with
      a fraction column is refused.
- [ ] Task: `pipeline/direct/vegetation_type/source.py` — window tiling of the envelope's 5070
      footprint (≤ 2,000 px, disjoint, deterministic order) and the `exportImage` request with the
      `Final` parameter string (`RSP_NearestNeighbor`, S16, `esriNoDataMatchAny`). Tests: tiling
      covers the footprint exactly once; a response with the wrong dimensions/pixel type is refused;
      no network (recorded fixture from `ROW-CAP-ANALYSIS` `tiles/*.tif`).
- [ ] Task: `reducer.py` — per-window pixel counting in EPSG:5070, cell assignment by reprojected
      pixel centre, NoData by legend non-membership → `evt_code=-9999`, 1 % cutoff → `-1`, dropped
      mass reported. Tests: on the eight retained windows, rows/cell within `classes_per_cell.json`
      tile range; `sum(pixel_count)` per cell (incl. sentinels) equals footprint pixel count; a
      two-window straddle fixture sums correctly.
- [ ] Task: `rows.py` + `support.py` — reducer output → arrow rows with `class_system`, legend codes,
      `observed_at`/`data_available_at` from the cited release constants, `release_day` broadcast;
      band-major sort by `cell_lat`; ~250,000 rows/part. Test: part count and row order on a
      synthetic 3-band frame.
- [ ] Task: `watermark.py` — folder-listing resolver + metadata digest (spec FR-2). Tests: four
      outcomes from recorded fixtures; the sticky `current` answer carries no instant
      (`evacuation_zones/watermark.py:42-48` reasoning).
- [ ] Task: Staging. `stage.py` writes one `window-<n>.parquet` under
      `layer=vegetation-type/staging/<version>/` per finished window (precedent
      `pipeline/direct/burn_severity/stage.py`); `forward.py` resumes from the staged set, publishes
      the base only when every window index is present, then deletes staging. Tests: resume after a
      simulated timeout re-fetches only missing windows; a partial staged set never publishes.
- [ ] Task: `adapter.py` + `forward.py` + `products.py` + `__main__.py` — `DirectWriterContract`
      shape as `pipeline/direct/watersheds/forward.py:49-70`; dry-run default, `--apply`; publishes
      through `fill_one_lane_day`/the static path (`gap_fill.py::_fill_static_day`) so the ladder is
      derived with the 1B banding and the availability generation advances last. Tests: dry-run
      writes nothing; `UNCHANGED` when the watermark is `current`.
- [ ] Task: `pipeline/validation/vegetation_type.py` — N-point `identify` reconciliation (spec
      FR-6). Tests: a mismatch names point, cell and source response; a class in the cell's `Other`
      mass is reported as `cutoff`, not `mismatch`.
- [ ] Task: NoData probes — record one coastal window (−124.6, 47.9) and one 49 °N window as
      fixtures; assert the sentinel behaviour the reducer assumes. Findings go to
      `docs/lanes/vegetation-type.md` §5.
- [ ] Task: Lattice — `tests/test_layer_import_contract.py:142-301` must stay green with the new
      files; no cross-slug import; no write outside `layer=vegetation-type/`.

### 1D — Registration (lane registry + executor, shadow)

- [ ] Task: `LaneRegistration` in `pipeline/parquet/lane_registry.py` beside `:766-796`;
      `adapter=_source_direct_refusal("agri_data_service.pipeline.direct.vegetation_type")`,
      `nature="static_lookup"`, `watermark=_vegetation_type_watermark`, `publication_lag_days=0`,
      `history_floor` = LF2025 release day, `floor_basis` citing `docs/lanes/vegetation-type.md §2/§3`.
      Test in `tests/parquet/test_lane_contract.py`: the four `__post_init__` rules
      (`lane_contract.py:172-201`) hold; a copy with `publication_lag_days=1` raises.
- [ ] Task: `_spec(VEGETATION_TYPE_DIRECT_LANE_ID, ...)` in `execution/job_executor_service.py`
      beside `:597-627`; daily, own phase offset, `timeout_seconds` sized for one bounded resumable
      turn, `selection_policy` per spec FR-7. Hand-extend `EXPECTED_SCHEDULES` in
      `tests/test_job_executor_service.py:38-41`. Confirm the generic static census reaches the lane
      through `LANE_REGISTRY` (`resolve_static_lane`, `lane_contract.py:272`) — if not, record the
      gap in `evidence/` as a finding.
- [ ] Task: Read the executor container memory limit from Railway (read-only) and record it in
      `docs/lanes/vegetation-type.md` §5 with the band sizing arithmetic (spec NFR-1, OQ-1).
- [ ] Verification: one integrated sweep of `services/agri-data-service` by the monitor; the
      Python quality receipt refreshed; `/code-review high` on 1C+1D together; `RUNBOOK.md` § added
      (pick the number at write time); verdict in `evidence/review-1CD.md`; then one production
      dry-run turn (`--apply` withheld) recorded as `evidence/dry-run-1.json`. Publication
      (`--apply`) is a separate, owner-acknowledged step [checkpoint marker]

---

## Phase 2: Serving reader, slider capability, agent tools, static coverage semantics

Goal: the published lane is readable at every zoom with a named shape, appears in the slider
catalogue as a snapshot, and answers the agent at the selected place.

- [ ] Task: `planes/vegetation_type.py` — bbox + rung read over `kind=observed` only
      (`planes/watersheds.py:45` pattern); composition / dominant-class projection / refusal shapes
      per spec FR-8; fractions from `pixel_count`; legend join from the vendored CSV. Tests in
      `tests/parquet/test_vegetation_type_serving.py` (model:
      `tests/parquet/test_watersheds_serving.py`): each shape, the budget boundary, `release_day`
      and `class_system` on every response.
- [ ] Task: Static coverage semantics — assert `resolve_static_lane` reports `current` vs `stale`
      vs `source_empty` vs `watermark_unread` distinctly for this lane, and that "current" and "not
      looked at" are different strings in the coverage inventory (layer-lanes §1a). Test with the
      fake object store.
- [ ] Task: TS reader in `src/lib/server/services/parquet-trpc-readers.ts` (pattern `:2187-2209`);
      Zod row schema for the three shapes; `rejectFutureDay` not applicable — a requested day is
      ignored with the vintage stated. Test: `src/__tests__/services/parquet-trpc-readers*.test.ts`
      (or the file that covers watersheds) extended with the three shapes.
- [ ] Task: `environmental.getVegetationType` in `src/lib/server/trpc/routers/environmental.ts`
      (near `:585` `getSoilField`); input bbox + zoom; output carries `shape`, `vintage`, rows.
- [ ] Task: Slider capability row in `parquet-slider-capabilities.ts:100-114`; registry entry
      `vegetation-type` in `src/lib/map/layer-registry.ts` after `:284-293` (`vegetation`), with
      `warehouseLayerName: "vegetation-type"`, `panelId: "vegetation"`, label "Vegetation Type
      (LANDFIRE EVT 2025)". Hand-spell the new name in
      `src/__tests__/services/parquet-slider-capabilities.test.ts` and
      `src/__tests__/lib/map/layer-registry.test.ts` (never import the constant under test).
- [ ] Task: Count registrations against `.max(32)` (`regional-evidence-tools.ts:30`); record the
      number in `evidence/`. Then `src/lib/server/services/vegetation-type-tools.ts` with
      `vegetation_type_at_point` (composition, vintage, K nearest cells with km) and
      `vegetation_type_in_area`; temporal proximity is the typed single-vintage refusal. Register in
      `regional-evidence-tools.ts:69` beside `LAND_CONTEXT_TOOLS`. Tests in
      `src/__tests__/services/regional-evidence-tools.test.ts` and a new
      `src/__tests__/services/vegetation-type-tools.test.ts`: success, refusal, out-of-envelope point.
- [ ] Task: Availability — confirm the static-lane availability generation carries one row per
      `(release_day, rung)` for all four rungs and that `_LATEST.json` advanced last
      (`pipeline/parquet/availability_index.py`); assert from the fake store.
- [ ] Verification: one integrated sweep (Python `tests/parquet` + `npm run test` scoped to
      `src/__tests__/services`, `src/__tests__/lib/map`, plus `tsc`); `/security-review` on the two
      tools (user-supplied coordinates/bbox); `/code-review high`; verdict in `evidence/review-2.md`;
      then a live API trace against production at z9/z10/z11/z13 saved as
      `evidence/api-shapes-<date>.json` [checkpoint marker]

---

## Phase 3: The raster rung (needed at BOTH ends)

Goal: a categorical PMTiles archive of LF2025 EVT over the envelope, catalogued and readable, so
z9–z10 (over budget for rows) and z11+ (finer than the lattice) have a picture. Alternative if the
archive cannot ship: FR-8's dominant-class projection is drawn at z10 and z9 is a stated refusal.

- [ ] Task: Confirm the catalogue location with the owner (spec OQ-3). Default
      `geo.raster_release` (`drizzle/0000_baseline.sql`, read via `geo.published_raster` in
      `raster-catalog.ts:44-100`). Record the answer in `evidence/`.
- [ ] Task: `scripts/raster/build-evt-mosaic.py` — assemble the S16 windows (re-fetched with the
      same `source.py` tiling, or read from staging if retained) into one EPSG:5070 COG; nodata by
      legend non-membership. Test (`scripts/raster/tests/` or inline `--self-check`): the mosaic's
      pixel at each of the five PLAN §2.1 identify points equals the identified value.
- [ ] Task: `scripts/raster/build-evt-tiles.py` — `WarpedVRT` over the XYZ pyramid grid
      (`scripts/raster/AGENTS.md` §tiles) with `Resampling.nearest`; **truecolour RGB PNG** from the
      legend `R,G,B` (1,069 classes exceed a 256-entry palette); tiles written in ascending id;
      z0–z14. Test: a tile's pixel colour maps back to exactly one legend class.
- [ ] Task: `scripts/raster/verify-evt-tiles.py` — sample ≥ 200 land points through `identify`,
      read the tile pixel at z13, assert colour → class agreement ≥ 99 %; report disagreements by
      point. A wrong-CRS mosaic fails this.
- [ ] Task: `scripts/raster/publish-evt-raster.py` — upload to R2 under `raster/vegetation-type/…`
      (`§env` credential reading from `publish-soil-rasters.py`), then insert the release row with
      categorical `color_ramp`, `unit="evt_code"`, `scale_divisor=1`, `source_release="LF2025"`,
      `license_name` confirmed against landfire.gov terms (spec NFR-5), bounds from the mosaic.
      Order: upload → verify bytes → register.
- [ ] Task: `environmental.getPublishedVegetationTypeRaster` in
      `src/lib/server/trpc/routers/environmental.ts` calling
      `getPublishedRasters("vegetation-type")`; leave `getPublishedSoilRasters` (`:583`) as is and
      record the sibling finding. Test: the procedure returns the row's `archiveUrl`, `bounds`,
      `minZoom`/`maxZoom`, `attribution`.
- [ ] Task: Update `scripts/raster/AGENTS.md` — the EVT product, the truecolour decision, measured
      build time and archive size.
- [ ] Verification: one sweep (`tsc`, scoped vitest, Python self-checks); `/code-review`; verifier
      independently re-runs `verify-evt-tiles.py` and records the agreement figure in
      `evidence/raster-verify-<date>.json`; verdict in `evidence/review-3.md` [checkpoint marker]

---

## Phase 4: Map layer, legend, hover, panel IA, About, live evidence

Goal: a user sees the type layer honestly labelled, hovers a composition, reads the 30 m picture at
high zoom, and the About page states what ships.

- [ ] Task: `src/lib/environmental/vegetation-type-legend.ts` — generated table
      `evt_code → {name, groupCode, groupName, phys, lifeform, rgb}` from the vendored CSV via
      `scripts/generate-vegetation-type-legend.mjs`; test asserts the embedded CSV sha256 equals the
      Python pin (`legend.py`) so client and tiles cannot drift; exports
      `VEGETATION_TYPE_LIFEFORM_COLORS` (10) and `VEGETATION_TYPE_SENTINEL_LABELS`.
- [ ] Task: `src/components/map/layers/VegetationTypeLayer.tsx` — raster source via
      `createPmtilesSource` (`src/lib/map/sources.ts:108-110`) at z ≥ 9 from
      `getPublishedVegetationTypeRaster`; lattice fill by dominant class at z ≤ 8 from
      `getVegetationType`; hover/click at every zoom reads the lattice; exports its colour constants
      for the legend. Register in `LayerManager` (see `src/components/map/AGENTS.md`), and the
      `renderKind: "component"` registry entry from Phase 2 gets its component. Tests: the layer
      adds no source when the catalogue returns `[]` (inert row, not a broken source); the hover
      card renders composition + vintage + cell size.
- [ ] Task: `src/lib/map/layer-legends.ts` — spec for `vegetation-type` importing
      `VEGETATION_TYPE_LIFEFORM_COLORS` from the renderer module (rule 1, `layer-legends.ts:8-11`);
      labels list the 10 lifeforms with "n finer classes". Extend the legends test file's
      hand-spelled toggle list.
- [ ] Task: `src/components/panels/VegetationDetails.tsx` — three subsections (Vegetation Type /
      Greenness (NDVI) / Occurrences) under the existing `panelId: "vegetation"`; vintage badge;
      resolution statement (spec NFR-3); the one type-vs-greenness sentence; no anomaly computed.
      Rename the heading in `src/components/map/layer-panel/dock-sections.ts:43` (`DETAILS_LABELS`)
      only if the `designer` review asks; no new `PanelId`. Component tests for the three headings.
- [ ] Task: `src/app/about/page.tsx` — add the LANDFIRE row (spec FR-12) near `:233-244`; rewrite
      `src/__tests__/app/about-page.test.tsx:45-48` to assert the new claim (vintage, ~0.005°
      cells, 30 m picture, public domain) by hand.
- [ ] Task: `src/components/map/AGENTS.md` — the raster-over-lattice split, why hover reads the
      lattice, the legend generation.
- [ ] Task: Live browser evidence — standalone
      `conductor/tracks/vegetation_type_landfire_evt_20260918/evidence/playwright.evt.config.ts`
      with `baseURL` = the deployed app, no `webServer`; a spec that toggles the layer at z6, z10,
      z13, hovers, and screenshots the badge and legend to `evidence/browser-<date>/`. **Never** the
      repo `playwright.config.ts`.
- [ ] Verification: one integrated sweep (`npm run test`, `tsc`, `next build` if the monitor
      requires); `/code-review`; `designer` verdict on the panel IA; browser evidence attached;
      `RUNBOOK.md` § updated; verdict in `evidence/review-4.md` [checkpoint marker]

---

## Risks carried into execution

| risk | phase | mitigation in this plan |
|---|---|---|
| executor memory unknown; grid derivation has no memory guard | 1 | read the limit first (1D); 1.0° bands asserted ≤ 5 M rows and ≤ 1/3 memory in a test; banding is exact so bands can shrink without changing results |
| `first` on dropped keys is only lawful if the legend nests | 1 | the functional-dependency test over the vendored CSV runs on every sweep |
| z9–z10 cannot serve composition rows at any cap | 2, 3 | three named shapes in the reader; raster is the picture; dominant-class projection is the stated fallback |
| raster catalogue may not belong in Postgres under the pivot | 3 | OQ-3 asked before publish; default is the brief's `raster-catalog.ts` path |
| `about-page.test.tsx` forbids the word LANDFIRE | 4 | flipped deliberately with a hand-spelled positive claim |
| concurrent RUNBOOK writers | all | § number chosen at write time; never `git add conductor/` wholesale |

---

## Partitions

Candidate disjoint write partitions per phase, for `metadata.json` (`confidence: hypothesis`,
`computed_at_commit: ec172e88`; re-verify `owns` with a grep when HEAD has moved). `status: future`
files do not exist yet.

| phase | partition id | owns (existing) | owns (`status: future`) | shared-read only |
|---|---|---|---|---|
| 1A | `p1a-grid-keys` | `services/agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py`, `services/agri-data-service/src/agri_data_service/warehouse/parquet/AGENTS.md`, `services/agri-data-service/tests/parquet/test_tiers.py` | `services/agri-data-service/tests/parquet/test_grid_per_tier_keys.py` | `pipeline/parquet/derivation.py` |
| 1B | `p1b-banding` | `services/agri-data-service/src/agri_data_service/pipeline/parquet/derivation.py`, `services/agri-data-service/src/agri_data_service/pipeline/parquet/objectstore.py`, `services/agri-data-service/src/agri_data_service/pipeline/parquet/AGENTS.md`, `services/agri-data-service/tests/parquet/test_derivation_and_drain.py`, `services/agri-data-service/tests/parquet/test_objectstore_writer.py` | `services/agri-data-service/tests/parquet/test_banded_derivation.py` | `tiers.py` (reads `band_height_degrees` after 1A lands — sequence 1A before 1B, or 1B owns the `TierDerivation` field addition and 1A stops at `GridAggregation`) |
| 1C | `p1c-lane-package` | — | `services/agri-data-service/src/agri_data_service/pipeline/direct/vegetation_type/{__init__,__main__,source,legend,reducer,rows,support,watermark,stage,adapter,forward,products}.py`, `services/agri-data-service/src/agri_data_service/pipeline/direct/vegetation_type/data/LF2025_EVT.csv`, `services/agri-data-service/src/agri_data_service/warehouse/schemas/vegetation_type.py`, `services/agri-data-service/src/agri_data_service/pipeline/validation/vegetation_type.py`, `docs/lanes/vegetation-type.md`, `services/agri-data-service/tests/direct/test_vegetation_type_*.py`, `services/agri-data-service/tests/parquet/test_vegetation_type_schema.py` | `pipeline/direct/watersheds/*` (template), `pipeline/direct/burn_severity/stage.py` |
| 1D | `p1d-registration` | `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py`, `services/agri-data-service/src/agri_data_service/execution/job_executor_service.py`, `services/agri-data-service/tests/test_job_executor_service.py`, `services/agri-data-service/tests/parquet/test_lane_contract.py`, `conductor/RUNBOOK.md` (one new § only) | `conductor/tracks/vegetation_type_landfire_evt_20260918/evidence/*` | imports 1C's `products.py`/`watermark.py` — sequence after 1C |
| 2 | `p2-python-plane` | — | `services/agri-data-service/src/agri_data_service/planes/vegetation_type.py`, `services/agri-data-service/tests/parquet/test_vegetation_type_serving.py` | `planes/watersheds.py`, `parquet_ops/serving.py` |
| 2 | `p2-ts-reader-slider` | `src/lib/server/services/parquet-trpc-readers.ts`, `src/lib/server/trpc/routers/environmental.ts`, `src/lib/server/services/parquet-slider-capabilities.ts`, `src/lib/map/layer-registry.ts`, `src/__tests__/services/parquet-slider-capabilities.test.ts`, `src/__tests__/lib/map/layer-registry.test.ts` | — | `src/lib/server/services/parquet-plane-client.ts` |
| 2 | `p2-agent-tools` | `src/lib/server/services/regional-evidence-tools.ts`, `src/__tests__/services/regional-evidence-tools.test.ts` | `src/lib/server/services/vegetation-type-tools.ts`, `src/__tests__/services/vegetation-type-tools.test.ts` | `src/lib/server/services/land-context-tools.ts` (pattern) |
| 3 | `p3-raster-scripts` | `scripts/raster/AGENTS.md` | `scripts/raster/build-evt-mosaic.py`, `scripts/raster/build-evt-tiles.py`, `scripts/raster/verify-evt-tiles.py`, `scripts/raster/publish-evt-raster.py` | `scripts/raster/build-soil-tiles.py`, `scripts/raster/publish-soil-rasters.py` |
| 3 | `p3-raster-read` | `src/lib/server/trpc/routers/environmental.ts` (the one new procedure; disjoint in time from `p2-ts-reader-slider`, which must land first) | `src/__tests__/services/raster-catalog.test.ts` | `src/lib/server/services/raster-catalog.ts` (unchanged) |
| 4 | `p4-layer-legend` | `src/lib/map/layer-legends.ts`, `src/components/map/AGENTS.md`, the `LayerManager` file under `src/components/map/` (locate by grep before claiming) | `src/components/map/layers/VegetationTypeLayer.tsx`, `src/lib/environmental/vegetation-type-legend.ts`, `scripts/generate-vegetation-type-legend.mjs`, `src/__tests__/components/map/layers/VegetationTypeLayer.test.tsx`, `src/__tests__/lib/environmental/vegetation-type-legend.test.ts` | `src/lib/map/sources.ts` |
| 4 | `p4-panel-about` | `src/components/panels/VegetationDetails.tsx`, `src/components/map/layer-panel/dock-sections.ts` (label only, if asked), `src/app/about/page.tsx`, `src/__tests__/app/about-page.test.tsx` | `src/__tests__/components/panels/VegetationDetails.test.tsx` | `src/stores/panel-store.ts` (no new `PanelId`) |
| 4 | `p4-browser-evidence` | — | `conductor/tracks/vegetation_type_landfire_evt_20260918/evidence/playwright.evt.config.ts`, `conductor/tracks/vegetation_type_landfire_evt_20260918/evidence/evt-layer.spec.ts`, `conductor/tracks/vegetation_type_landfire_evt_20260918/evidence/browser-<date>/*` | `playwright.config.ts` (never used, never edited) |

Sequencing constraints the coordinator must encode: 1A → 1B → 1C → 1D (each a push); within
Phase 2, `p2-python-plane` before `p2-ts-reader-slider` before `p2-agent-tools` (the tools call the
reader); `p3-raster-read` after `p2-ts-reader-slider` because both edit `environmental.ts`; within
Phase 4, `p4-layer-legend` and `p4-panel-about` are disjoint and may run in parallel;
`p4-browser-evidence` runs last against the deployed build.
