---
type: evidence
---

# Rung coverage census — which layers have the right Parquet rungs, and does the census see them

Read-only investigation for `environmental_postgres_retirement_20260904`, answering acceptance
criterion 3 ("coverage is complete AND the rungs are right"). Every claim below carries a
`file:line`. This document **refutes and refines** the track brief's "known starting facts" — see
"What changed since the brief was written" immediately below.

## What changed since the brief was written

The brief's premise — *"`build_gap_census` walks only `GAP_FILL_ZOOM_TIER`, so lane-days missing at
other rungs are invisible"* — was true **through 2026-09-01** and is **false today**. Two separate
fixes landed:

1. **2026-08-25** (`239a079`→`549346f`): a *manual* whole-bucket ladder census/repair was built —
   `parquet-drain --selection ladder` (`pipeline/parquet/drain.py:44-58`) — discovering **1,040
   lane-days across eleven lanes** invisible below z13 (`RUNBOOK.md:7305-7307` pre-prune). This tool
   was never wired into the automatic hourly tick and **has never been run against production**.
2. **2026-09-02**: the *automatic* census the hourly cron calls, `build_gap_census`, itself became
   ladder-aware — `pipeline/parquet/gap_fill.py:868-887` now calls `build_lane_census`, which for
   every series/static lane also runs `_ladder_repair_census` (`gap_fill.py:580-625`) and reports
   `ladder_repair_days` / `ladder_out_of_scope_days` per lane (`gap_fill.py:245-338`) and in the
   aggregate (`gap_fill.py:890-913`). Confirmed by `pipeline/parquet/AGENTS.md:297-301` ("Until
   2026-09-02 `build_gap_census` walked `GAP_FILL_ZOOM_TIER` and nothing else... The hourly tick
   repairs its own ladder now") and `drain.py:53`.

**The remaining blind spot is narrower than the brief describes, not zero** — see Q2.

**A brand-new regression has to be weighed against this fix.** On 2026-09-02 the geometry lanes' z9
derivation started raising `TierWriteError ... IOException: Can't find the home directory at
'/nonexistent'` in production — DuckDB's `LOAD spatial` opened with no extension directory (root
cause and fix: `gapless_parquet_publication_20260901/evidence/p3-runtime-blockers-repair.md:344-348`).
`parquet-drought`, `parquet-evacuation-zones` and `parquet-fire-perimeters` are confirmed hit and were
still held (dead-lettered, `replay_oldest`) at the 2026-09-04 handoff. **Point lanes derive with
Polars and were never affected.** The fix is in `152feca` (current HEAD), but no production
re-verification has been captured, so **any base-complete day these three lanes wrote between
2026-09-02 and the 152feca deploy is a fresh, unmeasured ladder hole layered on the 2026-08-25
backlog.**

## Per-layer table

Engine: **DuckDB spatial** = `GeometrySimplification` (`ST_SimplifyPreserveTopology`, opens a DuckDB
session via `warehouse/parquet/tiers.py::_load_spatial`). **Polars** = `GridAggregation` (floor +
re-aggregate, no WKB, no DuckDB session).

| layer | lane slug | rungs built | rungs the renderer requests | engine | census coverage | verdict |
|---|---|---|---|---|---|---|
| drought | `drought` | z13 base + z9/z5/z0 — **209/209 complete 2026-08-25** (`layer-sessions/drought.md:52-55`) | z0(<5)/z5(5-8)/z9(9-12)/z13(13+), all `native_polygon` (`src/lib/map/layer-render-contract.ts:435-441,495`) | DuckDB spatial (`warehouse/schemas/drought.py:97`) | ladder-aware (in-scope); whole-bucket walk never run | **matched 2026-08-25, z9 derivation broke 2026-09-02**; status since unmeasured. Only layer already drawing polygons from Parquet (`getParquetDrought`) |
| weather-observations | `weather-observations` | z13+3 — **20/20** (`layer-sessions/weather-observations.md:52-55`) | event_point: `aggregate_cell`/`heatmap`/`cluster` coarse→middle, `raw_point` detail (`layer-render-contract.ts:360-365,488`) | Polars (`warehouse/schemas/weather_observations.py:119`) | ladder-aware; unaffected by the DuckDB regression | matched as of last measurement |
| vegetation NDVI | `vegetation` | z13+3 — **990/1195, 205 INCOMPLETE** (`layer-sessions/vegetation.md:52-55`) | continuous field, fixed 0.25° support, `tessellated_cell` every band (`layer-render-contract.ts:538`) | Polars (`warehouse/schemas/vegetation.py:83`) | ladder-aware; the 205 ARE visible | **ladder short — 205 lane-days, plus 1,026 backlog days `vegetation-catch-up` has not exported** |
| fire-perimeters | `fire-perimeters` | z13+3 — **45/45 2026-08-25** (`layer-sessions/fire-perimeters.md:52-55`) | `native_polygon` all bands (`layer-render-contract.ts:493`) | DuckDB spatial (`warehouse/schemas/fire_perimeters.py:99`) | ladder-aware; whole-bucket walk never run | **matched 2026-08-25; confirmed hit by the DuckDB bug, dead-lettered, unverified since.** Still draws from Martin/Postgres |
| sensors | `sensors` | z13+3 — **1/26, 25 INCOMPLETE** (`layer-sessions/sensors.md:52-55`) | event_point (`layer-render-contract.ts:489`) | Polars (`warehouse/schemas/sensors.py:108`) | ladder-aware | **ladder short — 25 of 26 base days lack a full coarse ladder** |
| watersheds | `watersheds` | z13+3 — **1/1**; HierarchicalDissolve HUC12→HUC10(z9)/HUC8(z5)/HUC6(z0) (`warehouse/schemas/watersheds.py:101,104`) | `native_polygon` all bands, no `minzoom` | DuckDB spatial (`warehouse/schemas/watersheds.py:99`) | ladder-aware; `static_lookup`, so its ladder census is UNCAPPED (`gap_fill.py:717-728`) | matched, but only 1 day published — thin evidence base |
| evacuation-zones | `evacuation-zones` | z13+3 — **1/1** (`layer-sessions/evacuation-zones.md:52-55`) | `native_polygon` all bands (`layer-render-contract.ts:496`) | DuckDB spatial (`warehouse/schemas/evacuation_zones.py:121`) | ladder-aware | **matched 2026-08-25; confirmed hit by the DuckDB bug, dead-lettered, unverified since** |
| burn-severity | `burn-severity` | z13+3 — **4/4** (`layer-sessions/burn-severity.md:52-55`) | `native_polygon` all bands (`layer-render-contract.ts:494`) | DuckDB spatial (`warehouse/schemas/burn_severity.py:95`) | ladder-aware | matched 2026-08-25; **not named among the DuckDB casualties but not confirmed spared.** Renders today as Postgres MVT — 2,341,323 vertices / 37.5 MB / **cold-read 28.4 s** |
| fire-detections | `fire-detections` | z13+3 — **8,359/8,359** (`layer-sessions/fire-detections.md:57-60`) | event_point, detail band `aggregate_cell` not `raw_point` — FIRMS has no raw rung (`layer-render-contract.ts:366-373,486`) | Polars (`warehouse/schemas/fire_detections.py:152`) | ladder-aware | **matched, and it already has a direct-to-Parquet writer** (`fire-detections-direct-forward`); its Postgres exporter is backfill-only |
| water-gauges | `water-gauges` | z13+3 — **91/91** (`layer-sessions/water-gauges.md:52-55`) | event_point (`layer-render-contract.ts:487`) | Polars (`warehouse/schemas/water_gauges.py:123`) | ladder-aware | matched; has its own direct writer |
| signal-plane | `signal-plane` | z13+3 — **1,338/1,560, 222 INCOMPLETE** (`layer-sessions/signal.md:52-55`) | **Not in `LAYER_RENDER_CONTRACT` at all** | Polars (`warehouse/parquet/schema.py:206-228`) | ladder-aware | **ladder short (222 days) — the largest confirmed backlog by day count — and no renderer contract says what rung it must serve** |
| soil-survey | `soil-survey` | z13+3 declared, but **0/0 base days published** (`layer-sessions/soil-survey.md:52-55`) | shipped deviation: `aggregate_cell` every band instead of `native_polygon` (`layer-render-contract.ts:507-521`) | DuckDB spatial (`warehouse/schemas/soil_survey.py:113`) | N/A — nothing published | **the ladder question is moot: the base export has never completed.** Blocked on a 1,200 s timeout; the 200,000-key cap is confirmed gone |
| climate-field-* | 11 source-direct streams (`pipeline/direct/climate/products.py:142-242`) | z13+3 per stream (`pipeline/parquet/derivation.py:128`) | 9 toggles, `continuousFieldEntry` — `isoband`/`raster_surface`/`tessellated_cell` (`layer-render-contract.ts:463-472,383-390`) | Polars (`warehouse/parquet/snapshot_signal_product.py:223,258`) | ladder-aware in principle; **no per-product measured count found** | shadow writer; `climate-field-dew-point` and `climate-field-relative-humidity` withheld by `census_budget_exhausted` |
| soil-field-* | 8 source-direct streams (`pipeline/direct/soil/products.py:133-206`) | z13+3 per stream | `soil-moisture`/`soil-temperature`/`soil-vpd`, `continuousFieldEntry` (`layer-render-contract.ts:525-527`) | Polars (`snapshot_signal_product.py:285`) | same caveat | shadow writer, same activation wave |

## 1. Which layers are missing rungs the renderer will ask for?

Confirmed by direct measurement (`layer-sessions/*.md:52-55`, all dated 2026-08-25):

- **`vegetation`** — 205 of 1,195 base days ladder-incomplete, plus 1,026 unexported backlog days.
- **`sensors`** — 25 of 26 base days ladder-incomplete (96% of its published history).
- **`signal-plane`** — 222 of 1,560 ladder-incomplete, and no renderer contract at all.
- **`soil-survey`** — zero base days published; the ladder question does not yet apply.

**Suspected but unverified:** `drought`, `evacuation-zones`, `fire-perimeters` measured complete on
2026-08-25 but all three raised `TierWriteError` on z9 since 2026-09-02 and remained dead-lettered at
the 2026-09-04 handoff. Any day exported in that window is an uncounted hole. `burn-severity` and
`watersheds` use the identical DuckDB path but were not named — absence from the casualty table is
not proof of safety.

Work list for wave B: **run `parquet-drain --selection ladder` against production** (built and
dry-run verified since `549346f`, zero production runs), then re-measure all six geometry-derivation
lanes after `152feca` has had at least one production tick.

## 2. Is the gap census blind to any rung?

**Not literally blind anymore, but still short of "sees everything."**

- The hourly automatic census **is ladder-aware since 2026-09-02** and reports `ladder_repair_days`
  and `ladder_out_of_scope_days` at the top level of every report (`gap_fill.py:890-913`). The
  brief's premise as stated is refuted.
- Its ladder half is deliberately **SCOPED, not whole-bucket**: `_series_lane_census` bounds it to
  `(lane.history_floor, max(last_day, today))` (`gap_fill.py:794-809`), while `_static_lane_census`
  is uncapped (`gap_fill.py:717-728`). Days outside that scope are **counted** in
  `ladder_out_of_scope_days` but never selected for repair by the tick. Only
  `parquet-drain --selection ladder` walks the whole bucket (`drain.py:44-58`, `build_ladder_census`
  at `drain.py:531`) — and it has never run against production.
- **The two censuses' totals disagree and the arithmetic is not fully reconciled.** The whole-bucket
  discovery reported 1,040 lane-days across eleven lanes (11,510 z13 marks vs 10,473 at each coarse
  rung). The per-lane briefs from the same week sum to 452 (vegetation 205 + signal 222 + sensors
  25). Best inference — stated as inference, not fact — is that `fire-detections` closed 222 days in
  its 2026-08-25 direct-writer cutover (8,135/8,357 → 8,359/8,359) and the remaining ~360 sit in
  out-of-scope territory the two paths count differently.

Net: a green hourly tick promises "every rung *in scope* is right", not "every rung is right", and
the only tool that checks the rest has a working `--dry-run` and an unexercised `--apply`.

## 3. What does a coarse-zoom render cost right now?

**No Parquet-path coarse-zoom timing measurement exists in the repository.** What does:

- **The cost of NOT having a coarse rung, on the legacy path this track retires:** `burn-severity`'s
  tile function does zero simplification at any zoom — 541 rows / 2,341,323 vertices / 37.5 MB,
  **cold-read 28.4 s** (`multiscale_polygon_surface_20260901/evidence/native-polygon-baseline.md:76-77`).
  `watersheds` pre-rollup was ~7.6 MB per default viewport, which is why `geo.watershed_rollup`
  exists at all.
- **The closest Parquet-path browser evidence is qualitative:** the 2026-09-03 acceptance gate
  reports `state: ready` for fire density cells, climate air temperature z8, vegetation/water z5 and
  soil moisture z5 — pass/fail and screenshots, **no latency number**.
- **The nearest quantified figure is the coverage census, not a render:** ~28 s first request after
  an API deploy against an 8 s app timeout. That is criterion 3's tripwire, and it measures the
  coverage lookup.

**Cheapest way to get the missing measurement:** instrument the existing headless-Chromium acceptance
harness that already produces the 2026-09-03 screenshots to also record
`performance.getEntriesByType('resource')` timings for the Parquet API calls it already triggers at
the default PNW camera. No new infrastructure; one added assertion on a run that already exists.

## What I could not determine

- **Whether `burn-severity` and `watersheds` were hit by the 2026-09-02 DuckDB bug.** Same code path,
  absent from the casualty table — which may mean their buckets weren't sampled, not that they were
  spared. Needs a direct check of executor job-run history for those slugs.
- **The exact reconciliation of 1,040 vs 452 lane-days.** Inference given above; not verified against
  a fresh whole-bucket run, and none exists post-2026-08-25.
- **No census of any kind exists after the `152feca` DuckDB fix.** Every per-layer number here is
  dated 2026-08-25 — nine days stale — and the window includes both new exports and the derivation
  outage. True current blast radius is unmeasured and at least the numbers above.
- **Per-product ladder completeness for the 11 climate-field and 8 soil-field streams.** No
  layer-session brief exists for these; no per-stream census found anywhere in `conductor/`.
- **The 9-toggle vs 11-stream mismatch for climate-field.** `CLIMATE_FIELD_TOGGLE_IDS` yields nine
  entries; `CLIMATE_FIELD_PRODUCTS` registers what reads as eleven including three `SOIL_WETNESS_*`
  entries (`pipeline/direct/climate/products.py:215-233`). Unresolved whether they collapse into
  existing toggles, are hidden, or are a genuine discrepancy.
- **Whether `burn-severity`, `fire-perimeters`, `evacuation-zones` and `watersheds` render from
  Parquet at all today**, independent of ladder correctness. The baseline shows only `drought`
  reading `getParquetDrought`; the other five still read Martin tile functions against
  `geo.features`/`geo.watershed_rollup`. If still true, a correct ladder for those five is necessary
  but not sufficient for acceptance criterion 2.
- **`layer-legends.ts:412`** ("Drawn from zoom 7 in; below that the HUC12 outlines are too heavy to
  serve") appears to conflict with `watersheds-fill` having no `minzoom`. Unresolved whether it is
  stale legend copy or a different code path.

## ADDENDUM 2026-09-04 — the published signal lane has NO position columns (refutes a recorded fact)

Found by wave-C lane C2 with one bounded read-only probe of production, not by inspection:

```
layer=signal/kind=observed/zoom=13/year=2026/month=08/day=06/part-0.parquet
columns: support_key, signal_name, normalized_unit, cell_id, observed_day,
         normalized_value, observation_count, newest_observed_at,
         coverage_fraction, allowed_client_exposure
```

No `cell_longitude`, no `cell_latitude` — although `warehouse/parquet/schema.py:180-190` declares
both NON-NULLABLE. This **refutes the recorded note that the signal base carries positions and that
no re-export is owed.** A re-export IS owed, and until it lands the four spatial signal tools cannot
answer: raw, they raise `duckdb.BinderException`. C2 made them refuse `lane_columns_absent` instead,
naming the missing columns and the owed re-export, so the failure is typed rather than a stack trace.

Consequence for this track: `signal-plane` was already flagged as a D4 scope gap (it has live
PostgreSQL relations and no renderer contract). It now also has a **broken published lane**, so it
cannot be counted toward acceptance criterion 2 on any reading. Sequence the re-export before any
signal-plane drop packet.

## ADDENDUM 2026-09-04 — DuckDB geodesic functions take (latitude, longitude), and one lies

Also from C2, measured against DuckDB 1.5.4 rather than reasoned about:

```
ST_Distance_Spheroid(ST_Point(43.6, -116.2), ST_Point(43.62, -116.25)) = 4607.70 m   correct
ST_Distance_Spheroid(ST_Point(-116.2, 43.6), ST_Point(-116.25, 43.62)) = NaN         refused
ST_Distance_Sphere(  ST_Point(-116.2, 43.6), ST_Point(-116.25, 43.62)) = 5645.93 m   WRONG
```

C2's first draft used `ST_Distance_Sphere` in the ordinary `(lon, lat)` order and every distance came
back **23% too large, silently**. `ST_Distance_Spheroid` at least refuses with `NaN`; `_Sphere`
returns a plausible number. `ST_Distance_Sphere` is now banned outright in the agent reads
(`agent/parquet_reads.py:26-47`, pinned by `test_the_probe_point_is_bound_latitude_first`), and
`_Spheroid` is also the exact analogue of the retired PostGIS `::geography` distance.

**Every future Parquet lane doing spatial distance must bind latitude first.** This is the single
cheapest way to ship a wrong answer that looks right.
