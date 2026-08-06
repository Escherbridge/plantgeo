---
type: reference
---

# Reviewed historical plan artifacts

Checksum-governed input plans for the local historical replays. Every file here is either a
plan that some `agri-cli historical-*` command consumes, or the generator that authors one.

## Why the generators exist

`HistoricalEra5LandBackfillPlan` carries a field named `nasa_lattice_plan_checksum`. Nothing in
the codebase recomputes or cross-checks it — it is declared once at
`execution/historical_era5.py:143` as a bare `^[0-9a-f]{64}$` pattern, and the repository's own
unit test fills it with `"a" * 64`. It is therefore load-bearing provenance that the type system
cannot defend: a hand-typed value would look valid forever while pointing at nothing, and it is
folded into `historical_era5_plan_checksum()`, so a wrong value silently poisons the release
chain downstream.

`author_pnw_soil_moisture_plans.py` exists so that value is always *derived* from a real NASA
plan object rather than typed. It writes the NASA lattice plan and the ERA5 plan in one pass and
sets the ERA5 field from `historical_nasa_plan_checksum()` of the plan it just wrote.
`tests/test_pnw_soil_moisture_plans.py` re-derives the binding from the artifacts on disk, so a
hand-edit of either file fails the suite.

## The real prerequisite is spatial cells, not the checksum

The checksum is unenforced metadata. The *enforced* prerequisite lives in the writer:
`_require_era5_spatial_cells` (`execution/historical_writer.py`) raises "ERA5 persistence
requires the complete matching NASA sampling lattice in the warehouse" unless an
`agri.spatial_cell` row already exists for every `cell_key` in the ERA5 plan. Those rows are
created only by the NASA POWER persist path (`_ensure_spatial_cell`). So a NASA lattice plan
must genuinely be authored *and run* before ERA5 can persist — running ERA5 first fails at the
warehouse, not at validation.

Consequence for authoring: ERA5 cell keys must be reused verbatim from the NASA plan, and the
NASA plan must place its cells on whole-degree centroids, because
`require_governed_monthly_coverage` rejects any ERA5 cell that is not aligned to the reviewed
one-degree output grid.

## Cell geometry is deliberately borrowed

The PNW lattice is a strict subset of the already-reviewed North America lattice at
`infra/local-warehouse/plans/nasa-power-na-sampling-20220430-20260430-asof-20260721.json`,
copied cell-for-cell with its `grid_name`, `grid_resolution_m` and `cell_half_span_degrees`
intact. `_ensure_spatial_cell` compares existing rows with `ST_Equals` and refuses a key that is
"already governed by different geometry", so borrowing keeps a later full-coverage run
idempotent instead of making it collide.

## CDS prices retrievals by time, not by area

Measured 2026-08-05 against the CDS costing endpoint
(`POST /api/retrieve/v1/processes/derived-era5-land-daily-statistics/costing`) for
`derived-era5-land-daily-statistics`:

```
cost = 2 x variables x days      limit = 400 per request
```

`area` and `grid` are **not cost inputs at all**. A 2-variable 31-day request costs 124 whether
its area is the four-cell Boise box, Western North America, or the whole globe; grids of 1.0,
0.5, 0.25 and 0.1 degrees all cost 124 over the same area. The consequences are worth stating
plainly because they are counter-intuitive:

- **Widening the sampled extent is free** — same retrievals, same queue time, same cost. Only
  warehouse rows scale (2 x 1462 x cells, at ~529 bytes measured per `agri.signal_observation`
  row).
- What actually blows the limit is *time span in one request*: 2 months costs 244, 12 months
  costs 1460. The earlier "cost 930" rejection was a multi-month request, not a large area. The
  49-monthly-period decomposition exists to keep each request at 124 of 400, and it is the only
  reason the plan validates.
- There is real headroom: a quarterly period would cost ~368 and would cut 49 retrievals to 17.
  `Era5LandPeriod` forbids it (`max_length=31` on `days`, plus `require_consistent_month`), so
  this is a contract change, not a plan change. It is the single highest-leverage available
  speedup, because wall clock is dominated by CDS queue wait (~1 h per full month, so ~45 h for
  a 49-period plan) and not by area, bytes, or parsing.

## Extent is free but grid is frozen, so widen rather than densify

`requested_grid_degrees` is pinned to exactly 1.0 by `require_governed_monthly_coverage`, and
`AnalysisGridCell` keys must resolve to an `agri.spatial_cell` row. The canonical lattice is a
1-degree lattice, so a 0.1-degree ERA5 request would have no matching analysis cells to attach
its values to. **Grid tightening is blocked by the lattice, not by cost** — it requires a new
finer analysis lattice plus a contract change, whereas widening the extent needs neither.

The 1-degree output is a 10x downsample of ERA5-Land's native 0.1-degree grid, and that
downsample is not free of consequence: cross-checked against Open-Meteo's ERA5-Land archive,
soil *moisture* agrees to 0.00014 m^3/m^3 but soil *temperature* disagreed by up to 1.7 C at a
Cascades cell, where 1-degree averaging spans thousands of feet of relief. Treat 1-degree soil
temperature in complex terrain as representative of the cell, not of any point in it.

## The raw cache is keyed by the whole plan checksum

`historical_era5_raw_cache_paths` keys the download directory on
`historical_era5_plan_checksum(plan)`, which hashes `cells` and `requested_area` along with
everything else. Any re-plan therefore looks in a directory that does not exist and refetches,
even when the bytes it needs are one directory over. `load_cached_historical_era5_result`
accepts a `cache_plan_checksum` override and the checkpoint carries `raw_cache_plan_checksum`,
but `historical-era5-backfill` exposes no CLI flag for it, and the override is only sound when
the CDS request dict is unchanged — that is, same `area`, `grid`, `parameters`, `daily_statistic`,
`time_zone` and `frequency`. Reuse is therefore possible when *densifying inside an unchanged
envelope* and impossible when *widening the envelope*, because the cached NetCDF simply does not
contain the new cells and the nearest-point tolerance in `_era5_values_by_cell_and_date` will
reject them.

Note also that the download always contains the **entire `requested_area` at `requested_grid_degrees`**,
not just the declared cells: the four-cell PNW probe was downloading all 8 x 15 = 120 grid points
every month and discarding 116 of them. Declaring more cells inside an envelope you are already
requesting is pure upside.

## As-of times are frozen once a plan has run

`NASA_ACQUISITION_RELEASE_SET_AS_OF` must not be edited: that plan has been run and its checksum
is quoted by the ERA5 plan, so changing it orphans the binding and would re-persist the source
content under a new release. When receipts land after the acquisition as-of time,
`finalize_nasa_release_set` correctly declines to publish and the fix is the finalization
artifact plus `historical-nasa-finalize`, which advances only the release identity. The
generator hard-fails rather than silently rewriting a plan that is already on disk.

## Running the commands

Two environment facts bite every time:

- `_require_cds_credentials()` reads `os.environ` directly, while `Settings` loads `.env`
  through pydantic-settings, which does *not* populate `os.environ`. Listing `CDSAPI_URL` and
  `CDSAPI_KEY` in `.env` is not enough; they must be exported into the process.
- `require_local_source_loader_database_url()` rejects a loader DSN equal to `DATABASE_URL`.
  Because `.env` already points `DATABASE_URL` at the production proxy, `DATABASE_URL` has to be
  blanked in the process environment when passing the same target as the loader DSN.
