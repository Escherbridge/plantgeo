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
| `vapour_pressure_deficit_max` | era5_land | ✅ 3.32 kPa | Transpiration stress; the standard fire-weather ignition covariate. Absent from every source we ingest. |
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
- **Air Quality API** (CAMS) — PM2.5/PM10 and dust. Notably wildfire *smoke*, which pairs with
  FIRMS detections and burn severity to describe downwind impact, something no ingested source
  covers.
- **Ensemble API** — probabilistic members. The most direct route to genuine forecast uncertainty,
  which the platform currently has no upstream source for.
- **Climate Change API** (CMIP6 downscaled) — long-horizon scenarios.
- Satellite Radiation, Marine, Elevation — lower relevance to the current layers.

## Copernicus CDS

The CDS ERA5-Land lane is being retired for soil state: Open-Meteo redistributes the same
reanalysis at 0.1° (finer than the 1.0° CDS output grid the plans use), keyless, and moved
6.4M rows in an afternoon while CDS managed 2 of 49 periods against repeated 502s and dropped
connections. CDS remains the only route to genuinely CDS-only products — AgERA5
agrometeorological indicators, CEMS fire danger indices, and seasonal forecasts — none of which
Open-Meteo redistributes. Reach for it only for those.
