---
type: track-evidence
slug: gapless-parquet-product-ownership-census
status: p0-declared-frozen
observed_at: 2026-09-01
refreshed_at: 2026-09-02
supersedes_in_place: 2026-09-02T09:27-mdt-offline-draft
---

# P0/P3 product and ownership census — refreshed 2026-09-02

Read-only documentation refresh, slice W2-H of the 2026-09-02 parallel wave. HEAD at read time
`9052998`. `pipeline/parquet/lane_registry.py` and `execution/job_executor_service.py` were being
edited concurrently by another agent this wave.

- **First read:** 2026-09-02 21:40:51 MDT (`date` output captured before any source read).
- **Second read (close-out):** see the "Re-read confirmation" section at the bottom of this file.
- Every fact below is tagged **DECLARED** (from `LANE_REGISTRATIONS` / `LANE_SPECS` / `SNAPSHOT_PRODUCTS`
  source, unconditionally true regardless of what production is currently doing) or **MEASURED**
  (a receipt- or browser-observed number, true only at its stated timestamp). Nothing here mixes the
  two without saying so.

## Verdict

The physical product inventory is **28 time-bearing products**, unchanged in count from the
2026-09-02 09:27 offline draft this file replaces, but the ownership picture under it moved: the
eight NASA POWER climate-field streams are no longer merely "proposed" registrations — as of commit
`2b4cfef` (wave 1, `HEAD~1`) they are real `LANE_REGISTRATIONS` entries with a code-owned executor
spec (`pipeline/parquet/lane_registry.py:979-989`), and a ninth spec
(`climate-nasa-power-direct-forward`) is a genuine direct writer, not a plan. All nine are
**DECLARED executable, MEASURED shadow** — registered but excluded from
`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` (`conductor/RUNBOOK.md:204`: "the repository at `2b4cfef`
registers 47 (eight `parquet-climate-field-*` generic lanes plus `climate-nasa-power-direct-forward`),
none of them activated"). Everything else in the 47-spec registry (46 executable + the one terminal
`soil-moisture-parquet-backfill`) was already active at production release `e4490c3`
(`conductor/RUNBOOK.md:203-204`; `evidence/scheduler-handoff-20260902.md:40`).

Eleven of the 28 products (three NASA-POWER-derived soil-wetness breakdowns, one ERA5-Land VPD
breakdown, three ERA5-Land soil-moisture-depth breakdowns, four ERA5-Land soil-temperature-depth
breakdowns) have **no `LANE_REGISTRATIONS` entry, no executor spec, and no forward writer at all**.
Per `conductor/RUNBOOK.md:151`, "ERA5-Land (moisture, temperature, VPD) has no writer: it is
CDS-credential-blocked on the inert ingest service" — `config.py:153-154` confirms `CDSAPI_URL`/
`CDSAPI_KEY` is the gating credential and it is declared but not proven present in this read. No
floor, lag or cadence is invented for these eleven; the table below states plainly that
`LANE_REGISTRATIONS` declares none.

Four `static_lookup` streams (`calendar`, `evacuation-zones`, `soil-survey`, `watersheds`) and one
Postgres-only surface (`interventions`) are excluded below as not time-bearing; they are listed for
completeness in "Excluded surfaces."

## Table 1 — nine database-backed time-bearing lanes plus the transitional `signal` plane

### 1a. Identity and ownership

| product / lane slug | nature (DECLARED) | provider (DECLARED) | current writer/owner lane (DECLARED spec, MEASURED active/shadow) | legacy owner (DECLARED) |
|---|---|---|---|---|
| `burn-severity` | release_series | MTBS ArcGIS fire-year cohort releases, quarterly (`lane_registry.py:731`) | `mtbs-forward` (ACTIVE) sources into `geo.features`; `parquet-burn-severity` (ACTIVE) projects it (`job_executor_service.py:532-543`, `:502`) | `plantgeo-cron-mtbs` (`MTBS_OWNER`, `job_executor_service.py:246`), fenced not deleted |
| `drought` | release_series | US Drought Monitor (USDM), weekly | `postgres-drought` (ACTIVE, `job_executor_service.py:369-374`) + `parquet-drought` (ACTIVE) | `plantgeo-ingest-cron` (`INGEST_CRON_OWNER`) |
| `fire-detections` | daily_series | NASA FIRMS MODIS_SP, rolling NRT lookback (`FIRMS_DAY_RANGE`, `lane_registry.py:788`) | `fire-detections-direct-forward` (ACTIVE, `job_executor_service.py:506-516`) owns `>= 2026-08-25`; `postgres-firms` (ACTIVE) + `parquet-fire-detections` (ACTIVE, `writer_ceiling=2026-08-24`) own the rest | `plantgeo-fire-detections-forward` (`DIRECT_FIRE_OWNER`) direct; `plantgeo-ingest-cron` for the Postgres bridge |
| `fire-perimeters` | daily_series | NIFC WFIGS current-incidents view | `postgres-fire-perimeters` (ACTIVE) + `parquet-fire-perimeters` (ACTIVE) | `plantgeo-ingest-cron` |
| `sensors` | daily_series | NOAA/NWS `api.weather.gov` | `postgres-sensors` (ACTIVE) + `parquet-sensors` (ACTIVE) | `plantgeo-ingest-cron` |
| `signal` (transitional) | daily_series | NASA POWER + Open-Meteo ERA5-Land archive, dual producer (`lane_registry.py:836-839`) | **No `postgres-signal` spec exists in `_POSTGRES_SPECS`** — `parquet-signal` (ACTIVE) only projects what Postgres already holds; still no recurring forward NASA POWER/ERA5-Land ingestion owner in the executor, unchanged from the 2026-08-28 report's finding | none registered |
| `vegetation` | daily_series | Sentinel-2 L2A NDVI | `postgres-vegetation` (ACTIVE) + `vegetation-catch-up` (ACTIVE) + `parquet-vegetation` (ACTIVE) | `plantgeo-ingest-cron` |
| `water-gauges` | daily_series | USGS NWIS instantaneous/daily values | `water-gauges-direct-forward` (ACTIVE, `job_executor_service.py:518-530`) owns `>= 2026-09-02`; `postgres-streamflow` (ACTIVE) + `parquet-water-gauges` (ACTIVE, `writer_ceiling=2026-09-01`) own the rest | `plantgeo-water-gauges-forward` (`DIRECT_WATER_OWNER`) direct; `plantgeo-ingest-cron` for the bridge |
| `weather-observations` | daily_series | Open-Meteo current-conditions poll, `ingest/open_meteo.py` `WEATHER_LAYER` (`lane_registry.py:920`) | `postgres-weather` (ACTIVE) + `parquet-weather-observations` (ACTIVE) | `plantgeo-ingest-cron` |

### 1b. Floor, lag, cadence, source-ceiling rule (all DECLARED, `lane_registry.py:724-929`)

| product | floor (basis) | lag (days) | cadence (days) | source-ceiling rule |
|---|---|---|---|---|
| `burn-severity` | 2020-11-24 — day one of five MTBS release dates measured 2020-11-24..2024-08-22, `:727-742` | 7 | 1 (deliberate — an irregular release series takes no honest cadence step, `:733-735`) | latest established MTBS release date; no fixed `writer_ceiling` |
| `drought` | 2022-08-09 — MEASURED against production 2026-08-22: min(valid_date), 209 releases, 1,045 rows, `:747-761` | 4 | 7 (USDM publishes weekly, floor lands on a Tuesday) | latest confirmed weekly USDM release |
| `fire-detections` | 2000-11-01 — MODIS_SP floor, four eligible detections that day, `:783-793` | 2 | 1 | today − 2 days, clamped by `writer_ceiling = 2026-08-24` (day before the direct writer's 2026-08-25 floor, `pipeline/lanes/fire_detections.py:26`) |
| `fire-perimeters` | 2025-07-28 — residue of the hourly `_Current` poller's oldest isolated row; the documented 2020-01-01 floor has no fetcher wired and is deliberately unused, `:799-812` | 1 | 1 | today − 1 day; no fixed `writer_ceiling` |
| `sensors` | 2026-07-29 — NWS keeps a rolling ~6-day window; `geo.features` is append-only so the floor is static even though the source's isn't, `:816-828` | 1 | 1 | today − 1 day |
| `signal` | 2022-04-30 — whole plane's measured extent 2022-04-30..2026-08-06 across both producers, `:831-841` | 9 — the LARGER of ERA5-Land's measured 9-day lag and NASA POWER's 5-day lag, deliberately not the smaller, `:838-840` | 1 | today − 9 days |
| `vegetation` | 2022-08-05 — deepest governed forecastable plane, 2022-08-05..2026-08-04, `:862-870` | 7 — MEASURED median 7-day gap between observation days (cloud screening), worse than nominal 5-day Sentinel-2 revisit, `:869` | 1 | today − 7 days |
| `water-gauges` | 2026-05-24 — dense-record start; the code constant `USGS_DAILY_VALUES_EARLIEST=2022-08-05` is explicitly borrowed from vegetation and NOT source-imposed, `:880-893` | 2 — UNVERIFIED same-day-to-next-day provisional value, `:892` | 1 | today − 2 days, clamped by `writer_ceiling = 2026-09-01` (day before the direct writer's 2026-09-02 floor, `pipeline/lanes/water_gauges.py:39`) |
| `weather-observations` | 2026-08-01 — **FALLBACK, explicitly not measured**: the producer this lane exports (`ingest/open_meteo.py` `WEATHER_LAYER`) "has no contract content at all: no declared cadence, horizon, historical depth or known-gaps list," `:912-928` | 2 — borrowed from the hourly ingest-all tick, not measured, `:927` | 1 | today − 2 days; RUNBOOK section 0.26.8 owes a written contract and a `min(geo.feature_observation_day)` measurement before this row can leave FALLBACK status |

### 1c. Observed tail (MEASURED 2026-09-01) and terminal-state contract

| product | observed tail, 2026-09-01 (`conductor/RUNBOOK.md:158-172`) | terminal-state contract (DECLARED, `foundation/parquet/lane_contract.py:54-56`, `parquet_ops/wire.py:131,170,188`) |
|---|---|---|
| `burn-severity` (Burn History/MTBS) | latest selectable `2024-08-22`; none reported — cumulative release, polygon render visually coherent | `release_series`: each required rung day resolves to `PublishedDay`, `GovernedAbsenceDay` or `DayNotWritten`; most days are a correctly governed absence between quarterly releases |
| `drought` | **not sampled** in the 2026-09-01 browser pass; no row in RUNBOOK's Production timeline evidence table | `release_series`, same ladder as above, weekly step |
| `fire-detections` | latest selectable `2026-08-30`; catalogue listed 448 non-data days; assessment: "route ownership and latest-day semantics RED" | `daily_series` ladder; forecastable (`method/monte_carlo/fire_detections.py`, horizon 30d) |
| `fire-perimeters` | **not sampled** | `daily_series` ladder; explicitly NOT forecastable (`lane_registry.py:801-805`) |
| `sensors` | **not sampled** | `daily_series` ladder; forecastable (`method/monte_carlo/sensors.py`, horizon 30d) |
| `signal` | **not sampled** (its constituent snapshot products ARE sampled — see Table 2/3) | `daily_series` ladder; forecastable (`method/monte_carlo/signal.py`, horizon 30d) |
| `vegetation` | **not sampled** | `daily_series` ladder; forecastable (`method/monte_carlo/vegetation_ndvi_forecast.py`, horizon 30d) |
| `water-gauges` | latest selectable `2026-09-01`; missing tail `2026-09-02` only — "current-day freshness good; future ceiling invalid" | `daily_series` ladder; forecastable (`method/monte_carlo/water_gauges.py`, horizon 30d) |
| `weather-observations` | **not sampled** | `daily_series` ladder; explicitly NOT forecastable — no `method/monte_carlo/weather_observations.py` exists, `:921-922` |

## Table 2 — eight NASA POWER source-direct climate-field lanes (`pipeline/direct/climate/products.py`)

All eight share provider **NASA POWER**, nature **daily_series** (DECLARED not forecastable — no
`method/monte_carlo` module claims a horizon for any climate field, `lane_registry.py:965-967`),
cadence **1**, and `LANE_REGISTRATIONS` entries built by the genexpr at `lane_registry.py:979-989`.
Legacy owner is **none** for all eight — `_SOURCE_DIRECT_SLUGS` means `plantgeo-ingest-cron` never
produced a day of them (`job_executor_service.py:319-321`).

| product / stream | floor (DECLARED basis) | lag (DECLARED) | current writer (DECLARED spec, MEASURED shadow) | observed tail, 2026-09-01 (MEASURED) |
|---|---|---|---|---|
| `climate-field-air-temperature-max/-mean/-min` | 2026-08-07 — day after `CANONICAL_SNAPSHOT_LAST_DAY=2026-08-06` (`products.py:63,124`) | 5 — `CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS`, MEASURED (`execution/coverage_census.py` `PUBLICATION_LAG_DAYS['nasa-power-daily']`, `products.py:75-77`) | `climate-nasa-power-direct-forward` (SHADOW, `job_executor_service.py:556-576`) + `parquet-climate-field-air-temperature-{max,mean,min}` (SHADOW, mutually exclusive with the direct writer via `conflicts_with`) | latest selectable `2026-08-06`; tail `2026-08-07..09-02` (27 days), "contiguous unpublished tail" |
| `climate-field-dew-point` | 2026-08-07, same basis | 5 | same pair (SHADOW) | `2026-08-06`; 27-day tail |
| `climate-field-precipitation` | 2026-08-07, same basis | 5 | same pair (SHADOW) — **note:** absent from `SNAPSHOT_PRODUCTS`; served only via `coverage.py:52-58` `DEDICATED_SLIDER_PRODUCT_LAYERS`, not through the manifest-verified snapshot-read path the other six climate fields use | `2026-08-06`; 27-day tail |
| `climate-field-relative-humidity` | 2026-08-07, same basis | 5 | same pair (SHADOW) | `2026-08-06`; 27-day tail |
| `climate-field-wind-speed` | 2026-08-07, same basis | 5 | same pair (SHADOW) | `2026-08-06`; 27-day tail |
| `climate-field-shortwave-radiation` | 2026-06-01 — day after `SHORTWAVE_RADIATION_SNAPSHOT_LAST_DAY=2026-05-31`, nine weeks earlier than meteorology because the canonical snapshot's own `ALLSKY_SFC_SW_DWN` ledger stopped there (`products.py:65-69,190`) | **75 — DECLARED, EXPLICITLY UNMEASURED.** `CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS` is "5 (measured meteorology lag) plus the 67-day gap between the canonical snapshot's meteorology and solar last days, plus three days of slack... MEASURE POWER's own solar edge and replace it" (`products.py:79-85`; `lane_registry.py:951-962`) | same pair (SHADOW) — also absent from `SNAPSHOT_PRODUCTS`, same `DEDICATED_SLIDER_PRODUCT_LAYERS` fallback as precipitation | latest selectable `2026-05-31`; tail `2026-06-01..09-02` (94 days), "severe contiguous unpublished tail" |

## Table 3 — eleven ERA5-Land/NASA-POWER-derived dedicated products, NO lane registration

None of the eleven products below has a `LANE_REGISTRATIONS` entry, a `LANE_SPECS` executor id, or a
declared floor/lag/cadence anywhere in the registry — `LANE_REGISTRY` (`lane_registry.py:1029-1031`)
simply has no key for any of these eleven slugs. They exist only as frozen `SNAPSHOT_PRODUCTS`
entries (`parquet_ops/snapshot_products.py`) with `forward_first_day=None`, meaning every one of
their days is proven by the immutable `prod-20260826-full-signal-v1` manifest and none extends
forward. This is the census's explicit "do not invent" boundary.

| product / stream | provider (DECLARED, from execution/AGENTS.md and the 2026-08-28 report) | forward writer | floor/lag (DECLARED) | observed tail, 2026-09-01 (MEASURED) |
|---|---|---|---|---|
| `soil-wetness-surface`, `soil-wetness-root-zone`, `soil-wetness-profile` | **NASA POWER** `GWETTOP`/`GWETROOT`/`GWETPROF`, fraction-of-saturation, keyless (`execution/AGENTS.md:15`; `docs/reports/data-lane-execution-ownership-2026-08-28.md:161-166`) | none — `SnapshotProduct(...).forward_first_day` is `None` (`snapshot_products.py:249-262`) | LANE_REGISTRATIONS declares none | RUNBOOK reports these together as "NASA soil wetness, all three depths": latest selectable `2026-08-06`; 27-day tail — same edge as the meteorology products despite having no registered lane |
| `soil-field-vpd` | Open-Meteo ERA5-Land `vapour_pressure_deficit_max`, CDS-gated (2026-08-28 report line 155) | none — `forward_first_day=None` (`snapshot_products.py:232-238`) | LANE_REGISTRATIONS declares none; CDS-credential-blocked per `conductor/RUNBOOK.md:151` and `config.py:153-154` | RUNBOOK's "ERA5 VPD" row: latest selectable `2026-08-02`; tail `2026-08-03..09-02` (31 days), "contiguous unpublished tail" |
| `soil-field-moisture-0-7cm`, `-7-28cm`, `-28-100cm` | Open-Meteo ERA5-Land depth products, CDS-gated (2026-08-28 report line 156) | none — **absent from `SNAPSHOT_PRODUCTS` entirely**, unlike the other products in this table; served only through `coverage.py:52-58` `DEDICATED_SLIDER_PRODUCT_LAYERS`'s raw-prefix fallback, with no manifest verification at all | LANE_REGISTRATIONS declares none; CDS-credential-blocked | RUNBOOK's "ERA5 soil moisture" row: latest selectable `2026-08-02`; tail 31 days, "contiguous unpublished tail **and coarse seams**" |
| `soil-temperature-0-to-7cm`, `-7-to-28cm`, `-28-to-100cm`, `-100-to-255cm` | Open-Meteo ERA5-Land depth products, CDS-gated (2026-08-28 report line 157) | none — `forward_first_day=None` (`snapshot_products.py:267-303`) | LANE_REGISTRATIONS declares none; CDS-credential-blocked | RUNBOOK's "ERA5 soil temperature" row (reported once for all four depths): latest selectable `2026-08-02`; 31-day tail |

Required rungs for every product in Tables 1–3 are the same fixed ladder: zoom `(0, 5, 9, 13)`
(`foundation/parquet/zoom.py:29`, enforced by the `_DAILY_PART`/`_MONTHLY_PART` regexes at
`snapshot_products.py:79-86`). No product in this census declares a partial rung set.

## Excluded surfaces (not time-bearing, or not Parquet)

| surface | reason | disposition |
|---|---|---|
| `calendar` | `static_lookup`, no time axis; floor is DERIVED as `min(history_floor)` across the twenty source-bearing lanes (`lane_registry.py:998,1002-1016`) | `parquet-calendar` ACTIVE |
| `evacuation-zones` | `static_lookup`, watermark-driven, `HistoryCapability(supported=False)` (`lane_registry.py:764-777`) | `postgres-evacuation-zones` ACTIVE, `parquet-evacuation-zones` ACTIVE |
| `soil-survey` | `static_lookup`, watermark-driven, vintage not a daily series (`lane_registry.py:844-857`) | no source ingestion spec registered; `parquet-soil-survey` ACTIVE (projects only what Postgres already has) |
| `watersheds` | `static_lookup`, exactly one load day exists (2026-08-07), a boundary snapshot not a series (`lane_registry.py:896-908`) | `postgres-watersheds` ACTIVE (`job_executor_service.py:391-408`), `parquet-watersheds` ACTIVE |
| `interventions` | Postgres-only by design; RUNBOOK section 0.26.1 keeps it out of Parquet entirely (`lane_registry.py:719`) | no Parquet product; excluded from `LANE_REGISTRATIONS` on purpose |

## Explicit unknowns (left unknown rather than invented)

1. `drought`, `fire-perimeters`, `sensors`, `signal`, `vegetation`, `weather-observations` have **no
   MEASURED observed tail** — the 2026-09-01 browser pass recorded in `conductor/RUNBOOK.md:158-172`
   did not sample them. Their rows above say "not sampled," not a number.
2. `climate-field-shortwave-radiation`'s 75-day lag is DECLARED but the source itself states it is
   "NOT measured against POWER's live edge" (`products.py:79-85`) — carried forward as UNMEASURED,
   not corrected here.
3. The eleven ERA5-Land/NASA-POWER-derived products in Table 3 have **no DECLARED floor, lag or
   cadence anywhere in the registry** — `LANE_REGISTRY` has no key for any of their eleven slugs.
   This census does not compute or infer one.
4. Whether `soil-field-moisture-*`'s missing `SNAPSHOT_PRODUCTS` registration (unlike its sibling
   `soil-wetness-*`, `soil-temperature-*` and `soil-field-vpd`, which are all registered even though
   frozen) is a deliberate exclusion or a gap is not answered by any file read for this census. It is
   recorded as an open question, not resolved.
5. Whether `CDSAPI_URL`/`CDSAPI_KEY` are actually absent in the current production environment (as
   opposed to merely undeclared in this read-only local checkout) was not re-verified against Railway
   for this documentation-only slice; the "CDS-credential-blocked" characterization is inherited from
   `conductor/RUNBOOK.md:151` and `execution/AGENTS.md:15`, not independently re-measured here.
6. Whether the 46 executable specs beyond the 9 wave-1 additions are ALL still active exactly as the
   2026-09-02 scheduler handoff recorded, or whether any have since been paused/dead-lettered, was not
   re-queried against `agri.job_definition`/`agri.job_work_item` for this slice; "ACTIVE" above is
   inherited from `conductor/RUNBOOK.md:203-206` and `evidence/scheduler-handoff-20260902.md`, both
   dated 2026-09-02, not re-measured.

## Re-read confirmation

`pipeline/parquet/lane_registry.py` and `execution/job_executor_service.py` were re-read at
**2026-09-02 21:54:15 MDT** (`date` call immediately before this file was finalized). Line counts
are unchanged from the 21:40:51 first read (1,043 and 1,696 lines respectively, `wc -l`), and a
structural recount of `LaneRegistration(`/`_spec(` construction sites matches the first read exactly.
No re-derivation was needed: `LANE_REGISTRATIONS` still has exactly 21 entries (12 database-backed +
8 source-direct climate + 1 calendar), and `LANE_SPECS` still has exactly 47 entries (46 executable +
1 terminal). If a future reader finds this note stale, treat every DECLARED value above as needing
re-derivation, not as still-frozen.

## Sources read

- `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py` (full read,
  lines 1-1043, including `LANE_REGISTRATIONS` at 724-989 and `CALENDAR_REGISTRATION` at 1001-1027).
- `services/agri-data-service/src/agri_data_service/execution/job_executor_service.py` (full read,
  lines 1-600 and 1150-1560, including `LANE_SPECS` composition at 360-597 and tick-state logic at
  1174-1214).
- `services/agri-data-service/src/agri_data_service/pipeline/direct/climate/products.py` (full read).
- `services/agri-data-service/src/agri_data_service/parquet_ops/snapshot_products.py` (lines 1-334,
  including all fourteen `SNAPSHOT_PRODUCTS` entries).
- `services/agri-data-service/src/agri_data_service/parquet_ops/coverage.py` (lines 1-90, including
  `DEDICATED_SLIDER_PRODUCT_LAYERS`).
- `services/agri-data-service/src/agri_data_service/pipeline/lanes/fire_detections.py`,
  `.../pipeline/lanes/water_gauges.py` (direct-writer start-day constants).
- `services/agri-data-service/src/agri_data_service/foundation/parquet/lane_contract.py`,
  `.../foundation/parquet/zoom.py` (nature and rung vocabulary).
- `services/agri-data-service/src/agri_data_service/execution/AGENTS.md` (NASA POWER vs ERA5-Land
  soil-wetness provenance, line 15).
- `services/agri-data-service/src/agri_data_service/config.py` (lines 140-170, CDSAPI credential
  declaration).
- `conductor/RUNBOOK.md`, lines 1-230 (LIVE section, wave-1 note, observed-tail table at 158-172,
  scheduler/writer ownership at 179-206).
- `conductor/tracks/gapless_parquet_publication_20260901/evidence/scheduler-handoff-20260902.md`
  (full read).
- `docs/reports/data-lane-execution-ownership-2026-08-28.md` (full read, used only for the
  soil-wetness/soil-field provenance table and product naming; superseded for ownership claims by
  `docs/reports/data-lane-execution-ownership-2026-09-02.md`).
- `git log`/`git diff e4490c3c..HEAD` for the two key files, to confirm the 38→47 spec delta is a
  real wave-1 addition (`2b4cfef`) and not a counting error in this census.
