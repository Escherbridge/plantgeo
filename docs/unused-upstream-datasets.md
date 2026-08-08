# Upstream datasets we are not pulling

Survey date 2026-08-06. Variable names are verified against the live API where marked ✅ —
they go straight into checksummed plan files, so an unverified name costs a full re-fetch.

Adding an Open-Meteo daily variable is one entry in
`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` (`execution/historical_open_meteo.py`), which is the
single source of truth for whitelist, warehouse `signal_name`, units, and bounds. Note that
changing a plan's `parameters` changes its `plan_checksum` and orphans its cache, so every
addition is a **new plan + new release set + fresh fetch**, never an edit in place.

## Verified available, not yet ingested

| Variable | Model | Status | Use |
| --- | --- | --- | --- |
| `soil_temperature_7_to_28cm_mean` | era5_land | ✅ 19.4 °C | Root-zone thermal stress. Whitelisted 2026-08-06. |
| `soil_temperature_28_to_100cm_mean` | era5_land | ✅ 15.7 °C | Deep soil profile. Whitelisted. |
| `soil_temperature_100_to_255cm_mean` | era5_land | ✅ 10.0 °C | Deep thermal reservoir. Whitelisted. |
| `vapour_pressure_deficit_max` | era5_land | ✅ 3.32 kPa — whitelisted, plan authored (`open-meteo-era5-land-pnw-vpd-20220430-20260430.json`); **backfill not yet launched** | Transpiration stress; the standard fire-weather ignition covariate. Absent from every source we ingest. |
| `snow_depth_mean` | era5_land | ✅ 0.0 m | Snowpack melt-out timing — a green-up onset predictor for montane lattice cells. |

## Verified trap

`et0_fao_evapotranspiration` is **accepted by `era5_land` but returns all nulls**. It needs
`model=era5` (0.25°, coarser than our 0.1° lane). This would not have errored — it would have
persisted as missing data and quietly weakened any model that used it.

## Unverified candidates — probe before planning

Open-Meteo archive, plausible but not confirmed on the archive endpoint (several were seen only
in forecast-API docs): `precipitation_sum`, `rain_sum`, `snowfall_sum` (resolution upgrade over
NASA POWER `PRECTOTCORR`); `shortwave_radiation_sum`, `direct_radiation`, `diffuse_radiation`
(the direct/diffuse split enables light-use-efficiency canopy modelling that NASA POWER's single
`ALLSKY_SFC_SW_DWN` cannot); `wind_gusts_10m_max` (fire spread — NASA POWER carries mean wind
only); `cloud_cover` (era5 only — ERA5-Land has no atmospheric column);
`growing_degree_days_base_0_limit_50` (trivially derivable in-house from temperature extremes
instead of depending on an unconfirmed field).

Per the Open-Meteo maintainer (open-meteo/open-meteo#1293), ERA5-Land wind is only resampled ERA5
forcing with no independent skill — request `era5` for wind rather than paying a duplicate call.

## Other Open-Meteo APIs — whole capabilities, not just variables

- **Flood API** (GloFAS river discharge) — feeds the water layer directly and would give it real
  history, rather than the point observations USGS NWIS provides.
  **Status (2026-08-06, `upstream_dataset_expansion_20260806` slice C):** lane built —
  `ingest/open_meteo_flood.py` + `execution/historical_glofas.py` validate, fetch, chunk, checkpoint
  and cache end to end (CLI: `historical-glofas-status` / `historical-glofas-backfill`). **Warehouse
  persistence is blocked**: `persist_glofas_flood_chunk` / `finalize_glofas_release_set` do not exist
  yet in `execution/historical_writer.py`, so the lane puts zero rows in `agri.signal_observation`
  today. New `support_key` values minted: `glofas-v3-0.1deg`, `glofas-v4-0.05deg`. New `signal_name`
  values: `river_discharge` plus six `river_discharge_ensemble_*` statistics — all invisible to ML
  until a new `agri.covariate_feature_schema` version. `infra/cron-flood/railway.json` is staged but
  inert; no Railway service should point at it until the persist verb lands, the plan JSON is copied
  into the cron image, and a volume backs `local_execution_root`.
- **Air Quality API** (CAMS) — PM2.5/PM10 and dust. Notably wildfire *smoke*, which pairs with
  FIRMS detections and burn severity to describe downwind impact, something no ingested source
  covers.
  **Status (2026-08-06, slice C):** lane built alongside GloFAS — `ingest/open_meteo_air_quality.py`
  + `execution/historical_cams.py` (CLI: `historical-cams-status` / `historical-cams-backfill`). Same
  persistence blocker as GloFAS: no CAMS equivalents of the `historical_writer.py` persist/finalize
  helpers exist yet. New `support_key` values: `cams-global-0.4deg`, `cams-europe-0.1deg`. New
  `signal_name` values: `particulate_matter_10`, `particulate_matter_2_5`, `carbon_monoxide`,
  `nitrogen_dioxide`, `sulphur_dioxide`, `ozone`, `dust`, `aerosol_optical_depth`,
  `ultraviolet_index`, `united_states_air_quality_index`, `european_air_quality_index`. CAMS
  publishes hourly only, so the lane reduces to one daily statistic per variable (mean for
  concentrations, maximum for indices) and requires ≥18 observed hours, using a new
  `insufficient_hourly_coverage` quality flag for a published-but-unreducible day.
  `infra/cron-air-quality/railway.json` is staged but inert for the same reasons as the flood config.
- **Ensemble API** — probabilistic members. The most direct route to genuine forecast uncertainty,
  which the platform currently has no upstream source for.
  **Status (2026-08-06, slice D):** lane built — `ingest/open_meteo_ensemble.py` +
  `execution/ensemble_forecast.py` fetch, bound, cache, parse and reduce ensemble members into the
  exact `ForecastReceipt.quantile_levels` / `ForecastValue.quantile_values` shape (CLI:
  `forecast-ensemble-status` / `forecast-ensemble-fetch`). Output today is a local checksummed
  staged-receipt document, not warehouse rows. The quantile columns themselves need no migration,
  but every `ForecastReceipt` requires a `forecast_run_id` whose `ForecastRun.forecast_method` is
  CHECK-constrained to `('sql_linear', 'ml')` in the database, the model, and
  `routes/forecasts.py:86` — none of which describes an upstream NWP ensemble. **Receipt persistence
  is blocked on widening that CHECK migration** (and its coordinated mirrors); that is separate work
  from this lane's already-complete quantile-carriage logic.
- **Climate Change API** (CMIP6 downscaled) — long-horizon scenarios.
- Satellite Radiation, Marine, Elevation — lower relevance to the current layers.

## NASA FIRMS fire-detection archive

A deep historical backfill (`ingest-backfill --source nasa-firms-archive`, back to
`FIRMS_ARCHIVE_EARLIEST_OBSERVATION` = 2000-11-01) is a separate capability from the live
`ingest-firms` cron already running every 3 hours — the cron covers the last few days; nothing walks
the 25-year archive.

**Status (2026-08-06, `upstream_dataset_expansion_20260806` slice E): DEFERRED**, per architect
ruling `firms-launch=DEFER`. Of the three preconditions the ruling named, two are already satisfied
by existing code for this write path — cap-truncation already fails loudly (`_run_backfill_chunk`
refuses and reports the retry chunk size rather than silently dropping the oldest rows), and
`geometry_id` is already set at write time (`_maintain_batch_geometry` runs unconditionally inside
every `ingest-backfill` / `ingest-firms` batch). The remaining gates before launch:

- **The `readObservationWindows` expression index does not exist yet.** It needs a btree over
  `to_date(substring(COALESCE(observedAt, updatedAt, polygonDateTime), 1, 10), 'YYYY-MM-DD')` on
  `geo.features`. Do not reuse `geo.feature_observation_day` (`drizzle/0015_tile_observation_day.sql`)
  for this — it derives the day via a session-`TimeZone`-dependent `::timestamptz::date` cast that
  can disagree with the slider's own `OBSERVATION_DAY` rule.
- **No cursor-file wrapper script exists for `ingest-backfill`.** Unlike the plan-checksummed
  `historical-*` lanes, the generic `ingest-backfill` verb has no persisted checkpoint of its own, so
  a wake-based scheduled task cannot safely self-drive across days the way the soil-temp walk does
  today; a new sibling to `durable-backfill.sh` is needed first.
- **`--since`/`--until` still need an explicit decision.** A full walk from
  `FIRMS_ARCHIVE_EARLIEST_OBSERVATION` is roughly 9,400 daily chunks.
- **`NASA_FIRMS_KEY` is not available locally** — it is Railway-only and absent from every local
  `.env` under `services/agri-data-service`.

## Copernicus CDS

### The soil lane is SUPERSEDED, not retired — nothing deleted, nothing dropped

Open-Meteo redistributes the same ERA5-Land reanalysis at 0.1° (finer than the 1.0° output grid
the CDS plans request), keyless, and moved 6.4M rows in an afternoon while CDS managed 2 of 49
periods against repeated 502s and SSL errors. Soil moisture and soil temperature are served from
Open-Meteo today and verified rendering to users.

No data was dropped because **this lane never persisted a warehouse row**: `agri.data_source`
holds no `era5-land` key, and the only residue is a git-ignored `.agri-local-runs/historical-era5/`
cache whose two checkpoints never reached `validated`. The code stays too — `historical_era5.py`
is the one working CDS integration template, and every product below reuses its shape.

Artifacts kept and marked rather than removed: `plans/era5-land-pnw-soil-*.json`,
`plans/era5-land-western-na-soil-*.json`, `infra/local-warehouse/plans/era5-land-na-sampling-*.json`,
their two generators, `execution/historical_era5.py`, `historical_era5_parquet.py`,
`execution/historical_writer/era5.py`, and the four `historical-era5-*` CLI verbs.

### What CDS is still the only route to

Tracked in `conductor/tracks/cds_only_products_20260808/`.

| Product | Dataset id | Host | Notes |
| --- | --- | --- | --- |
| AgERA5 agrometeorological indicators | `sis-agrometeorological-indicators` (pin `version: 2_0`) | `cds.climate.copernicus.eu` | 0.1° native — already matches our lattice. Daily, 1979→present, CC-BY. **Zero new credential plumbing.** Evaluate the `-timeseries` sibling first; it may avoid gridded NetCDF entirely. |
| CEMS fire danger indices | `cems-fire-historical-v1`, `cems-fire-seasonal` | **`ewds.climate.copernicus.eu`** | Closes a real gap: we have fire detections, perimeters and burn severity but no fire *danger* index. FWI/FFMC/DMC/DC/ISI/BUI/DSR plus US NFDRS and Australian McArthur, 1940→present. |
| Seasonal forecasts | SEAS5 / C3S originating-centre datasets | `cds.climate.copernicus.eu` | Long horizon. Ids not verified to the same standard as the two above. |

**CEMS blocker, surface it before planning:** EWDS is a 2024+ split-off from the classic CDS with
its own accounts, terms and API keys. The existing `CDSAPI_URL`/`CDSAPI_KEY` are scoped to the
classic host only, so CEMS needs a second registered key and a second URL/key pair through
`Settings`. The known failure mode — `dataset cems-fire-historical-v1 not found` — is really the
classic client pointed at the wrong host.

**Two traps that carry over from the retired lane:** the four-calendar-year window validator plus
exact day-for-day period coverage make time-splitting a plan impossible, and cell-splitting buys no
wall clock because retrievals are per-period. Intra-plan period concurrency is the only real lever.
AgERA5 adds one of its own: it appears to retrieve one variable + one statistic per call rather than
bundling a list, so N variables × statistics multiplies queue latency. Confirm cardinality on the
live request form before checksumming any plan.
