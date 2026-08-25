---
type: module-notes
---

# `analysis/` — read-only investigations over the Parquet warehouse

Sibling of `scripts/`, deliberately **outside** `src/agri_data_service/`, so nothing here is
bound by `tests/test_layer_import_contract.py`. Nothing in this directory writes to Postgres,
writes to the object store, or creates a local database file.

Chartered 2026-08-24 out of the wildfire/carbon session recorded in `conductor/RUNBOOK.md`
§0.41. Tracks: `regional_fire_risk_surface_20260824`, `rangeland_carbon_lane_20260824`.

## Why the memory ceiling is not advisory

`warehouse_session.py` sets `max_temp_directory_size='0GiB'`. That is not tuning — it is a
guard with a specific incident behind it.

USDM drought releases are CONUS-wide multipolygons of up to ~140,000 vertices and ~2.2 MB of
WKB each. A query of the shape

```sql
SELECT ... FROM cells c CROSS JOIN drought_polygons d WHERE ST_Contains(d.geom, c.point)
```

materialises one geometry reference **per output row**. At 1,568 cells × 1,045 polygons that
is 1.6 M rows each carrying a large geometry; DuckDB spilled until it consumed the host and
disrupted unrelated processes. With spilling disabled the same query raises `OutOfMemoryError`
in about a second and the machine is unaffected.

Two disciplines make the spatial work cheap enough that the ceiling is never approached:

1. **Clip before you probe.** USDM polygons cover CONUS; the analysis grid spans roughly
   −124.9→−111.1, 42.1→48.9. `ST_Intersection` against that envelope cuts the largest polygon
   from 140,352 vertices to 6,300 — a 22× reduction with **no precision loss inside the region
   actually probed**, so no cell can change category.
2. **One polygon per query.** Probe each severity band separately so exactly one geometry is
   ever in flight. After clipping, a full release costs ~0.1 s per band.

Simplification (`ST_Simplify`) was evaluated and rejected as the primary lever: at a 0.002°
tolerance it only reached a 4× vertex reduction, and unlike clipping it *can* move a boundary
and flip a cell's category.

## The leakage trap

`fire_risk_index.py` pins `FEATURE_WINDOW` to 1 Apr – 30 Jun and opens the outcome window on
1 Jul. This is the single most important thing in the module.

The first pass at this analysis used a 1 Jun – 6 Aug window for vegetation and soil. Coleman
Creek ignited **25 July**. Low NDVI inside that window is partly the *scar of the fire being
predicted*, which makes any skill estimate circular. Every reported figure below comes from
the separated windows. If you widen `FEATURE_WINDOW`, you invalidate the results.

## Measured results (2026 season, 1,470 cells, 492 burned)

Single-variable discrimination on the rangeland strata (greenness quartiles 1–2; 736 cells,
268 burned):

| Signal | AUC | Direction |
|---|---|---|
| composite `risk_index` | **0.725** | — |
| `vapor_pressure_deficit` | 0.697 | higher burns |
| `soil_temperature` | 0.677 | higher burns |
| `surface_moisture` | 0.605 | **lower** burns |
| `greenness` | 0.588 | higher burns |

Decile lift on the composite: 14.9 % burned in the lowest decile → 67.1 % in the highest.

**The composite only beats VPD alone by ~0.03.** Vapour pressure deficit is doing most of the
work. Do not present the composite as a substantial improvement over a single-variable screen.

### Why the index is stratified

VPD and soil temperature separate the dry interior from the wet coast, so an unstratified AUC
partly measures "the interior burns" rather than "dry springs burn". Within greenness
quartiles the VPD AUC runs 0.693 / 0.746 / 0.667 / **0.586** — strong in steppe and
transition, weak in closed forest. `add_risk_index` therefore scores quartiles 1–2 only.
**The index does not transfer to forest and must not be applied there.**

Within the sparsest quartile only, greenness flips to a strong positive predictor (0.674):
more spring fine fuel, more fire. That is the invasive-annual-grass pathway showing up
directly, and it is stratum-specific — do not generalise the sign.

## Two claims this module's own evidence refuted

Recorded because both are plausible, both were briefly believed here, and both are wrong.

1. **"Fire intensity is predictable from the same signals."** Raw `frp_sum` totals suggested a
   dramatic inversion (low-risk cells releasing ~47× more energy). `frp_sum` is summed over
   detections, so it confounds intensity with duration and extent. Normalised per detection the
   effect collapses to 12.8–29.8 MW with correlation **+0.131**. There is no intensity signal.
2. **"Large fires concentrate in low-risk cells."** Mean detections per burned cell by risk band
   were 1256 / 957 / 398 — but the medians are **16 / 84 / 13**. The pattern was a handful of
   megafire cells, not a gradient. Do not report it.

Consequence: this index predicts **where fire occurs**, not how much carbon a fire releases.
Fire-frequency targeting and carbon-protection targeting are separate problems and no evidence
here says they share a map.

## Standing caveats

- **One season, scored in-sample.** AUC 0.725 is optimistic; the model was fit and evaluated on
  2026. Honest validation needs additional fire years, which is gated on the historical
  `fire-detections` backfill — `parquet_duckdb_pivot_20260823` item B, where fire-detections is
  69 % of the 13,037-lane-day drain. Until that lands, treat these as associations.
- **Ignition is not modelled.** The index measures receptiveness to fire, not whether fire
  arrives. Coleman Creek came from a lightning outbreak.
- **Cells are ~28 km.** A 0.25° cell is a region, not a field. Nothing here is a siting tool.
- **The upstream detection feed caps records per request and drops the excess silently**, which
  bites hardest on exactly the large fire days that matter most.
