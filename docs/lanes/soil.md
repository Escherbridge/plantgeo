---
type: lane-contract
slug: soil
horizon: none
---

# `soil` lane (ERA5-Land soil state)

Eight object streams written by one lane, `soil-era5-land-direct-forward`: three moisture depths
(`soil-field-moisture-{0-7cm,7-28cm,28-100cm}`), four temperature depths
(`soil-temperature-{0-to-7cm,7-to-28cm,28-to-100cm,100-to-255cm}`) and VPD (`soil-field-vpd`). The
browser draws them as three toggles (moisture, temperature, VPD). Not to be confused with
`soil-survey` (SSURGO, static) or `soil-properties` (SoilGrids rasters).

## 1. Source system

The **Open-Meteo ERA5-Land archive** (`archive-api.open-meteo.com/v1/archive`, `models=era5_land`),
an intermediary redistributor of Copernicus ERA5-Land, NOT the CDS
(`warehouse/field_products/soil.py` module docstring). Keyless on the free host; the paid customer
host takes `OPEN_METEO_API_KEY` (`lanes/_providers/open-meteo.toml`). Weighted: one call per location
per request at 1 day and 8 variables.

## 2. Grain

One row per (cell, day, stream) on the `analysis-0p25` lattice (1,568 cells, 0.25-degree half-step
over the region envelope, keyed `sentinel2-ndvi-0p25deg:<lat>:<lon>`). A complete day carries values
at **exactly 1,470** cells per variable; the other 98 are ocean, where the archive answers `null`
(`pipeline/direct/soil/support.py` ERA5_LAND_VALUE_CELL_COUNT, measured on all 1,556 immutable days
for moisture and VPD).

## 3. History

Immutable snapshot through **2026-08-02** for all eight streams (`ERA5_LAND_SNAPSHOT_LAST_DAY`);
forward-written from **2026-08-03** (`SOIL_DIRECT_WRITER_START_DAY`). The registry's measured
complete-history floor is 2022-04-30 (`pipeline/parquet/lane_registry.py`
`_MEASURED_COMPLETE_HISTORY_FLOORS`). No writer may touch a day at or before 2026-08-02.

## 4. Edge

Candidate edge `today - 5` (Open-Meteo's documented ERA5-Land delay). The redistributor's real edge
wanders (nine days measured 2026-08-11), so an all-null answer at the candidate edge is the ordinary
shape of a day not yet mirrored, never evidence the source has nothing.

## 5. Forecast recommendation

None. No method projects a soil field; the lane claims no horizon.

## 6. Absence

An all-null day is a governed absence only when a LATER day is already published with values (the
mirrored-past proof). Without that proof it is refused and re-asked.

## 7. Known gaps

- The legacy writer reads the cell identities (`cell_id`, `coverage_fraction`) from
  `agri.spatial_cell`; the config strategy cannot (§8).
- Soil temperature's 1,470 is inherited from moisture/VPD (same model, same land-sea mask), not
  counted independently; a different count is refused loudly.

## 8. Config-lane strategy (`s-soil`, track `config_driven_ingestion_20260926`)

A dark `daily_series` TOML and strategy beside the legacy direct writer:

- **Files.** `services/agri-data-service/lanes/soil-era5-land-direct-forward.toml` (`executor =
  "legacy"`, `enabled = true`, mirroring the live legacy lane); `pipeline/lanes/soil/` (strategy key
  `soil.open_meteo_era5_land`; package notes in its `AGENTS.md`); `tests/lanes/soil/`.
- **Why `executor = "legacy"` is load-bearing.** `execution/lane_catalogue.py` removes a lane id from
  the legacy dispatch the instant a TOML with that id loads with `executor = "config"`, before
  `enabled` is read. A `config` value here would silently stop the production soil cron.
- **One day per request, as legacy.** 32 units of 50 cells (`31 x 50 + 18`) for one day: 1,568
  weighted calls, plus the 2-call probe = 1,570 under the 1,600 forward cap -- exactly one day per
  turn, the G0 legacy cap (33 requests, one day) restated in weighted calls. Gap-fill (off, S12;
  `gap_fill_enabled_at_gate = "G6"`, cron `20 3 * * *` pre-declared) affords one day per turn too.
  - *Deviation from spec §4.1's illustrative comment* ("one 14-day fan-out (1,568) + the probe"): a
    14-day span per chunk would cost the same 1,568 and answer the whole window, but every row's
    `selected_source_part_key`/`selected_source_part_sha256` and the day's `direct:` snapshot id
    would then name a 14-day request, so no config day would equal its legacy twin at G6, and the
    chunk digest (the S11 source digest) would change whenever ANY day of the span changed. Per-day
    requests keep lineage byte-equal and the S11 digest per-day. Revisit after G6 if the owner wants
    the cheaper backfill (it needs a per-day sub-document digest and a lineage-namespace decision).
- **Settlement.** 0 or 1,470 values per variable. One settlement per lane-day covers all eight
  streams, so a day where any variable is short -- even one variable unsettled while seven are
  whole -- is `refused_partial` (legacy published per product; see `pipeline/lanes/soil/AGENTS.md`
  "The day"). More than 1,470 is a `strategy_error` (mask moved). All-null is `Absent` only when
  every stream has a later published day (`DayContext.later_published_day`), else `unsettled`.
- **Probe and gate (S19).** `probe_edge` asks two centre cells over the 14-day window. The runner
  gates: an unvalued day newer than the probed edge waits (`newer_than_probed_edge`, not an alarm);
  `unavailable` (5xx, or a body the strategy cannot parse) and `deferred` (429) gate the whole
  window and the turn exits 0; quota refusals surface as `deferred_quota`, never as unpublished
  days.
- **Malformed bodies.** Every legacy attribution guard (location order, native node within half a
  box, GMT, one native node per cell, ISO day axis, reviewed value ranges) is restated in
  `pipeline/lanes/soil/archive.py` and raised inside `fetch`, so a bad chunk fails its unit as
  `strategy_error` and the day is held -- never a partial write.
- **Volatile columns.** `selected_source_release_retrieved_at` (moisture) and
  `selected_release_retrieved_at` (temperature), stamped by `rows` from the newest chunk
  `retrieved_at`; VPD has none.

### 8.1 G6 blockers (outside this slice's files)

1. **Cell identities are not shipped.** `pipeline/lanes/soil/support_cells.json` (1,568 entries from
   `agri.spatial_cell WHERE grid_name = 'sentinel2-ndvi-0p25deg' ORDER BY cell_key`) must be
   extracted once with the legacy support read and committed. Until then `rows` raises and every
   day is a `strategy_error`. Check the package data is included in the image (`src/` is copied
   whole today).
2. **The customer host makes the key mandatory.** `[endpoints.archive]` declares a
   `customer_host`, and `ingest/provider_client.py::provider_endpoint_request` treats an empty
   `OPEN_METEO_API_KEY` there as a configuration error (exit 78). The key is empty on the executor
   today (spec O8), so a config soil turn cannot run until the owner sets it; the schema forbids
   `optional_api_key` on a customer-host endpoint.
3. **G6 compare and the part-key host.** Config rows record the free host in
   `selected_source_part_key`; the legacy writer records the host it called, which becomes the
   customer host once the key is set. Legacy days written keyless compare equal; legacy days written
   keyed will differ on `selected_source_part_key`/`input_source_part_keys` only. That is a
   lineage-provenance decision (record the called host, or always the free one) for the owner at G6,
   not a volatile column.
4. **Ordinal collation.** Legacy ordinals and chunking follow Postgres `ORDER BY cell_key`; the
   strategy follows Python's sort. The identity parser refuses a document where they disagree, so
   the first extraction proves or refutes it.
