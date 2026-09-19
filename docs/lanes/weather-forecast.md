---
type: lane-contract
slug: weather-forecast
horizon: none
---

# weather-forecast lane

Source-of-truth spec for the `weather-forecast` layer lane, chartered 2026-09-19 by track
[`plantgeo_ml_service_20260918`](../../conductor/tracks/plantgeo_ml_service_20260918/spec.md)
FR-12. Owner, relayed by the concurrent session: *"let ml take over any projections, they don't need
to be in the lanes."*

**This lane has no writer in agri-data-service, and registering it is not a reversal of the
2026-09-19 deletion.** That decision was about ADMISSION: agri admits no provider projection, and
still ingests none -- the deleted ingest packages stay deleted and nothing in this service fetches
Open-Meteo forecast hours. `services/plantgeo-ml-service` writes the stream; agri reads it for
serving and registers the slug so its readers, census and slider catalogue can resolve it. The
registered adapter refuses an export and names the writing service.

## 1. Source system

Open-Meteo's NWP forecast product, fetched and admitted by
`plantgeo_ml_service/pipeline/sources/open_meteo.py` and written by
`plantgeo_ml_service/pipeline/weather_forecast_daily.py`, with the row assembly in
`plantgeo_ml_service/pipeline/weather_forecast_rows.py`. The variable catalogue and the schema live
in `plantgeo_ml_service/warehouse/weather_forecast.py`; `method/` in that service stays HTTP-free,
so the fetcher sits in `pipeline/sources/`.

Keyless. Eight published variables: `cloud_cover`, `precipitation`, `relative_humidity_2m`,
`temperature_2m`, `wind_direction_10m`, `wind_speed_10m`, and the two DERIVED components
`wind_u_10m` / `wind_v_10m`.

## 2. Cadence

**Daily.** Nature `release_series`, `cadence_days=1`, `publication_lag_days=0`.

`release_series` rather than `daily_series` because the partition day is the provider's own ISSUE
date, never a day anything was observed or verified. The future-ness of a row lives entirely in its
`valid_time` column. That is the `drought` pattern, applied to a product that issues daily instead of
weekly, which is why the cadence is 1: the step exists, and it is one day.

Lag 0: a run is settled the moment the provider issues it. Nothing about waiting makes an issue date
more final.

## 3. Historical horizon

**Floor `2026-09-18`, basis: first ML-service publication** -- the day the ML service's own lane
contract declares (`plantgeo_ml_service/warehouse/lanes.py`), matching the day the lane was
chartered.

It is not a measurement of archive depth and must not be read as one. Open-Meteo serves no forecast
archive, so a run older than the writer's first tick is unrecoverable; an earlier floor would invent
gap-days nothing can ever fill.

## 4. Grain

**`(cell_id, valid_time, variable)`** -- one variable reading, at one lattice cell, for one valid
instant. Nineteen columns, frozen at `e66dbc36` and pinned identically in both services
(`agri_data_service/warehouse/schemas/weather_forecast.py` and
`plantgeo_ml_service/warehouse/weather_forecast.py`).

`run_id` is constant within a partition, so it is provenance rather than part of the sort key -- but
the tier derivation keys on it anyway, so two runs can never merge into one coarse row.

`cell_id` is nullable ONLY so the coarse rungs may null it; the base rung always carries it, which
is what `base_non_null_columns` restores.

## 5. Known gaps and traps

- **`kind=forecast` under this slug is RESERVED and must stay empty.** Provider runs are
  deterministic, so a forecast partition's ensemble provenance (`random_seed`, `ensemble_size`)
  would have to be invented. The slot is held for an ML-corrected product.
- **The hourly precipitation timestamp labels the START of its accumulation hour.** The provider's
  own daily sums prove it; Open-Meteo's documentation says otherwise and is wrong
  (`.omc/research/forecast-s3-probe-20260919/`). The deleted code's one-hour shift is NOT ported --
  re-applying it silently moves every rainfall hour.
- **A multi-location response is a JSON array in request order**, and the pairing must be verified
  against the snapped coordinates rather than trusted positionally.
- **Wind: the provider answers speed and direction; this platform derives u and v.** The derived
  pair exists because a coarse rung averages components and recomposes the bearing -- a scalar mean
  of a bearing is not a bearing. Two consequences: `wind_speed_10m`'s statistic reads
  `derived_vector_magnitude` even at the base rung, where it is the provider's own scalar; and when
  the provider sends no wind, all four variables are absent together.
- **A null `value` carries a populated `missing_reason` -- at the BASE rung.** The coarse rungs null
  the reason, because a cell merging present readings with absent ones can name no single one.
  Never read a null value as a zero.
- **`weather-forecast` is not `weather-observations`.** Different slugs, different services,
  different meaning for the partition day.

## 6. Validation approach

- Schema parity between the two services: the nineteen fields, their types, nullability, order and
  the sort key are identical by construction and diffed by the ML service's parity tests.
- `tests/parquet/test_tier_derivation.py` derives a synthetic day at every rung and casts it back to
  the storage contract.
- `tests/parquet/test_lane_contract.py` pins the registration (nature, floor, lag, cadence, no
  forecast module) and asserts the ML tree ships no forecaster under this slug's own stem.
- Value plausibility is the writer's: each variable declares physical bounds in the catalogue, and
  an out-of-bounds reading is refused there rather than published and filtered here.

## 7. Forecast recommendation

**None, and that is a statement rather than an omission.** `forecast_module=None`,
`forecastable=False`. This service projects nothing: the lane already carries a provider's
projection as a dated publication, and layering a second projection over it would produce a forecast
of a forecast with no way to attribute either.

The ML-corrected product FR-12 reserves `kind=forecast` for is the only thing that would change
this, and it would be a new claim with its own module and its own gate.
