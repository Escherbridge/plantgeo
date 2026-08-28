---
type: reference
---

# Reviewed historical plan artifacts

Checksum-governed input plans for the local historical replays. Every file here is either a
plan that some `agri-service data historical-*` command consumes, or the generator that authors one.

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
`_require_era5_spatial_cells` (`execution/historical_writer/era5.py`) raises "ERA5 persistence
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

Two environment facts used to bite every time. Both were removed on 2026-08-08; the notes
survive because runbooks and older reports still describe the workarounds:

- `_require_cds_credentials()` read `os.environ` directly while `Settings` loaded `.env`
  through pydantic-settings, so a `.env` entry was inert and the pair had to be exported.
  `Settings` now carries `cdsapi_url`/`cdsapi_key`: a real environment variable still wins, and
  `.env` alone is sufficient.
- `require_local_source_loader_database_url()` rejected a loader DSN equal to `DATABASE_URL`,
  so `DATABASE_URL` had to be blanked when passing the same target. It now falls back to
  `DATABASE_URL` and accepts an identical value, so nothing has to be blanked.

## The Open-Meteo ERA5-Land archive plans

`build_open_meteo_ndvi_plans()` authors two artifacts:

| Plan | Cells | Chunks | Purpose |
|---|---|---|---|
| `open-meteo-era5-land-boise-ndvi-probe-20220430-20260430.json` | 16 | 2 | prove the whole path and finalize a release set inside one session |
| `open-meteo-era5-land-pnw-ndvi-lattice-20220430-20260430.json` | 1,568 | 32 | the real target: every `sentinel2-ndvi-0p25deg` cell |

**The lattice is derived arithmetically, never read from the warehouse.** `ndvi_lattice_cells()`
regenerates the fixed global 0.25-degree grid the way `ingest/vegetation.py` cuts it -- cell south/west
plus half a cell, rendered to four decimals, prefixed with the grid name, sorted by the resulting
string. Verified 2026-08-06 against production: the 1,568 generated keys and coordinates are
identical to `agri.spatial_cell`, with zero missing, zero extra and zero coordinate mismatches. A
generator that queried the warehouse would not be reproducible from a clone, and
`test_open_meteo_artifacts_regenerate_byte_for_byte` would have nothing to compare against.

Note the ordering: keys sort lexically, so the first cell is `42.1250:-111.1250` and the last is
`48.8750:-124.8750`. That is not a bug and it matches the producer.

**These plans mint no spatial cells.** Unlike the NASA lattice plans, whose whole purpose is to
establish `agri.spatial_cell` rows, this lane requires them to exist already and fails closed
otherwise. There is no NASA-plan checksum binding because there is no NASA lattice involved: the
cells came from the Sentinel-2 NDVI backfill.

**Chunk size is a request-count knob, not a cost knob.** Open-Meteo weights a request by
locations x variables x timesteps, so `chunk_cell_count` changes how many HTTP calls the run makes and
nothing about how much quota it consumes. It is part of the plan checksum, so changing it produces a
new plan rather than a resume that straddles two chunk shapes.

**No CDS credentials, no licence acceptance, no queue.** This lane needs neither `CDSAPI_*` nor a
browser licence click. The environment note above about exporting CDS credentials does not apply;
the note about blanking `DATABASE_URL` does.

**`OPEN_METEO_API_KEY` is optional and is deliberately absent from these plans.** A paid
subscription raises the quota that walls a full lattice crawl; it changes no requested datum. It is
an environment fact, so setting it does not change `plan_checksum` and does not orphan an existing
checkpoint or raw cache. Do not add it, or a host field, to a plan file. See
`execution/AGENTS.md` §historical_open_meteo, "Paid access is environment, not plan".

**Later single-variable lattice plans reuse the same 1,568-cell lattice wholesale, not
`ndvi_lattice_cells()`.** `open-meteo-era5-land-pnw-soiltemp-20220430-20260430.json` (four soil-
temperature bands) and `open-meteo-era5-land-pnw-vpd-20220430-20260430.json` (`vapour_pressure_deficit_max`,
an atmospheric covariate, not soil state) both carry the identical `cells` array as the moisture
lattice above -- copied, not regenerated -- because the lattice is fixed and re-deriving it per plan
would risk a silent mismatch this file's own test (`test_open_meteo_artifacts_regenerate_byte_for_byte`)
would not catch, since that test only covers the two plans `author_pnw_soil_moisture_plans.py` builds.
`chunk_cell_count` (50), `model`, `native_grid_*`, `support_key`, `time_zone` and the `source` block are
also byte-identical across all three plans; only `description`, `parameters` and `release_set_key`
differ, so each is genuinely a different `plan_checksum` and a different release set. Both are unrun:
`release_set_as_of` uses the same far-future placeholder as the moisture lattice, not a completion
forecast.

## The radiation plan is the one Open-Meteo plan that does NOT ride the NDVI lattice

`open-meteo-era5-nasa-power-lattice-radiation-20220802-20260802.json` requests
`shortwave_radiation_sum` over the **397-cell `nasa-power-0.5-degree` lattice** — the
`na-sample:1deg:*` cells, copied verbatim from
`nasa-power-western-na-weather-radiation-20220531-20260531.json` rather than regenerated, for the same
reason the soil-temperature and VPD plans copy the NDVI lattice.

**It is the one plan on `models=era5`, not `era5_land`, and the filename says so.** ERA5-Land
publishes no radiation flux through the archive endpoint and signals that with an all-null series
rather than an error, so the first authoring of this plan -- which inherited the lane's usual
`era5_land` -- fetched, validated, persisted and finalized 397 entirely empty series before anything
refused it. The lane now rejects such a plan at validation time. Choosing ERA5 costs spatial
resolution honestly: `support_key` is `era5-0.25deg` (~25 km) rather than `era5-land-0.1deg` (~9 km),
and the plan carries its own `data_source` key, `open-meteo-era5-archive`. See
`execution/AGENTS.md` §"The archive model decides which variables have values at all".

It exists to raise a ceiling, not to add a signal. NASA POWER's `ALLSKY_SFC_SW_DWN` lane is complete
at 397/397 cells and permanently stuck at **2026-05-31** behind that parameter's ~2-month publication
lag; Open-Meteo republishes the same daily quantity with ~6 days of lag under the **same
`signal_name` and the same unit, with no conversion**. Full rationale, the measured unit evidence, and
the three serving-side predicates that still exclude these rows live in
`execution/AGENTS.md` §"Shortwave radiation is a SECOND upstream".

Two consequences for authoring:

- **The lattice choice is load-bearing, not cosmetic.** Putting this signal on the NDVI lattice would
  give one `signal_name` two different coverage definitions, and would also miss
  `getPublishedClimateField`'s `cell.grid_name = 'nasa-power-0.5-degree'` predicate. Nothing in the
  lane hardcodes a lattice: `grid_name` and `grid_resolution_m` (55660 here, 27830 on the NDVI plans)
  are plain plan fields, and the writer compares spatial cells against `plan.grid_name`.
- **`window`, `release_set_as_of`, `chunk_cell_count`, `time_zone` and `transform_version` stay
  byte-identical to the three 2026-08-08 continuation plans; `support_key`, `model`, `native_grid_*`
  and the whole `source` block do not, and cannot.** Choosing `models=era5` fixes those per-product
  (`support_key = era5-0.25deg`, `source.key = open-meteo-era5-archive`), which is the point of the
  paragraph above. The window's `end_date` (2026-08-02) is the upstream frontier measured for this
  lane that day; it is shared rather than re-measured, so all four continuation plans cover one span.
  397 cells at 50 per chunk is 8 chunks against the 2,000 ceiling.
- **`transform_version` keeps the `era5-land` spelling on an ERA5 plan on purpose.** It is identity,
  not description: one of the four columns of `uq_source_release_identity`, projected as provenance by
  `agri.v_signal_timeseries_contract`, and inside `plan_checksum`. Renaming it would fork the identity
  of the 8 chunks already persisted under it and orphan this plan's checkpoint and raw cache, forcing
  a re-fetch of a quota-bound dataset to improve a label. See `execution/AGENTS.md` §"Shortwave
  radiation is a SECOND upstream".

## Plans are one half of the coverage loop

A plan authors work; `docs/layer-lane-standard.md` defines the loop it belongs to -- required days from the
lane's contract, minus observed days, minus governed absences, becomes the gaps a plan must close. A plan
that only walks forward leaves interior holes no verb can reopen.
