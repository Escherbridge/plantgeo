---
type: source-reconciliation
track: weather_forecast_parquet_lane_20260911
status: candidate-probed-not-admitted
checked_at: 2026-09-12T01:07:51.7473851Z
---

# Weather source reconciliation

The viable deterministic candidate is **Open-Meteo Single Runs, ECMWF IFS HRES,
sampled locations**, using `models=ecmwf_ifs`. A public, credential-free request
successfully returned one identified run and ten days of eleven hourly variables.
This is source-probe evidence; it is not admission of a production lane, a native
field, a probability product, or any historical-observation replacement.

## Existing implementation and limits

| Surface | Evidence in the checked tree | Meaning for this track |
| --- | --- | --- |
| Current-condition weather | `ingest/open_meteo.py::current_weather_url` calls `https://api.open-meteo.com/v1/forecast` with `current=` temperature, humidity, wind speed/direction and precipitation. It pins m/s, GMT and Unix time, but no model/run. `parse_current_weather` validates freshness and numeric bounds. | Preserve as sampled estimates. The endpoint name does not make the stored current values a forecast series. |
| Weather direct writer | `pipeline/direct/weather_observations/` owns the surviving Parquet writer; `ingest/AGENTS.md` records deletion of the PostgreSQL weather ingestion job on 2026-09-06. | Do not resurrect a PostgreSQL producer or use reanalysis to fill a sampled-current gap. |
| Archive adapter | `ingest/open_meteo.py::archive_daily_request` selects the archive host and explicitly pins ERA5 or ERA5-Land. | Reanalysis is a separate product, not evidence of operational forecast run identity. |
| Ensemble transport | `ingest/open_meteo_ensemble.py` implements bounded free/customer transport, explicit `models`, GMT, nearest-cell selection, coordinate batches, member field naming and source URL provenance. | Useful fetch/validation patterns; not a completed forecast publication plane. |
| Ensemble staging | `execution/ensemble_forecast.py` implements reviewed plans, chunk caches/checkpoints, strict hourly axes/member counts, empirical quantiles, null-member rejection and receipt checksums. Its `ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE` remains `blocked_forecast_method_check`. | Existing work is substantial staging scaffolding. It supplies no evidence of immutable forecast Parquet run publication, governed readers or active duties. Its old SQL blocker must not motivate a new environmental PostgreSQL write path. |
| Ensemble time | `EnsembleForecastPlan.issue_time` combines plan `issue_date` with UTC midnight. `execution/AGENTS.md`, “Issue time is plan-declared; availability is not”, explicitly says the provider answers current runs. Retrieval time and elapsed-hour flags prevent some false availability claims. | Those guards still do not identify the source initialization or pin one model run across requests. Do not promote plan midnight into provider issue or initialization time. |
| Ensemble support | Product constants declare ECMWF 0.4°, GEM 0.5°, GFS 0.25° and member counts 51/21/31. Requests select analysis-lattice coordinates. | Treat these as existing code assertions pending renewed source reconciliation. A declared grid resolution does not give the sampled locations native cell geometry. |

Paths in the table are relative to
`services/agri-data-service/src/agri_data_service/`.
The upstream expansion plan itself marks ensemble “built, schema-gated” and
records uncompleted probe/persistence work. No production system was inspected.

## Deterministic product and temporal identity

The [Single Runs documentation](https://open-meteo.com/en/docs/single-runs-api)
defines `https://single-runs-api.open-meteo.com/v1/forecast` with required
`run=YYYY-MM-DDTHH:mm`, a UTC initialization reference. The source describes
ECMWF IFS HRES as global O1280 support, nominal 9 km, hourly API output, ten-day
horizon and 00/06/12/18 UTC cycles. It states Cycle 50R1 applies from
2026-05-12 06 UTC; earlier archived 49R1 hindcasts begin 2024-03-14. These are
documented product facts, not a model-version field verified in the response.
Ordinary forecast series stitch recent runs; Single Runs preserves the requested
initialization. Initialization precedes availability and must not be relabelled
release time.

Proposed identity for the probed candidate is the tuple
`(open-meteo-single-runs, ecmwf_ifs, model-cycle, initialization-UTC, product-version)`.
Its exact credential-free request and content checksum also belong to every
source receipt. Preserve the requested run even though the JSON does not echo it.

The [provider metadata documentation](https://open-meteo.com/en/docs/model-updates)
separates initialization, conversion completion and API availability. Servers
converge asynchronously; it recommends allowing ten minutes after availability.
Metadata is for a particular model and server class, not a substitute for a
historical run's missing release receipt. API hourly output may interpolate a
coarser temporal source. Therefore record provider issue/release as absent unless
run-matched evidence supplies it; a scheduled delay or retrieval instant cannot
fill that field. Store PlantGeo fetch, admission and publication separately.

An archived run remains a forecast *at its source run*. In this probe, early valid
hours were already past at retrieval. They must not be described as newly
available operational predictions or scored as if PlantGeo possessed them at
initialization. The April 28, 2025 History selection remains owned by the existing
catalogue/reader reconciliation; this source cannot silently satisfy it.

## Public probe receipt

The final probe fetched at **2026-09-12T01:07:51.7473851Z**, received HTTP 200,
and reported **14,573 response bytes**. No API key was used. It selected:

- Endpoint: `https://single-runs-api.open-meteo.com/v1/forecast`.
- `models=ecmwf_ifs`, `run=2026-09-08T00:00`, `forecast_days=10`.
- Requested latitude/longitude **43.615, -116.2023**.
- `cell_selection=nearest`, `elevation=nan`, `timezone=GMT`,
  `timeformat=unixtime`, `temperature_unit=celsius`,
  `wind_speed_unit=ms`, `precipitation_unit=mm`.
- The eleven `hourly` fields listed below, in that order.

Returned coordinate: **43.620384, -116.15964**; returned elevation: **1056 m**;
GMT with UTC offset zero. Every array has 240 entries, from
**2026-09-08T00:00:00Z** through **2026-09-17T23:00:00Z** inclusive.
The response contains coordinates, generation duration, timezone/elevation,
hourly units and hourly values; it contains no run echo, model-version field,
provider release time or native grid-cell polygon.

| Field | Returned unit | Proposed temporal interpretation | Null entries |
| --- | --- | --- | --- |
| temperature_2m | °C | instant | 0 |
| relative_humidity_2m | % | instant | 0 |
| apparent_temperature | °C | instant, provider-derived value | 0 |
| dew_point_2m | °C | instant | 0 |
| cloud_cover | % | instant | 0 |
| pressure_msl | hPa | instant | 0 |
| precipitation | mm | preceding-hour accumulation | 1, at run hour zero |
| weather_code | wmo code | provider condition classification at valid hour | 1, at run hour zero |
| wind_speed_10m | m/s | instant | 0 |
| wind_direction_10m | ° | meteorological direction, to be bound to explicit vector convention | 0 |
| wind_gusts_10m | m/s | preceding-hour maximum | 1, at run hour zero |

The [general variable reference](https://open-meteo.com/en/docs) supplies units
and aggregation semantics. It also documents nearest-cell selection and
`elevation=nan` to disable statistical elevation downscaling. Returned coordinates
must be retained independently of requested coordinates. The precipitation and
gust window proposed for hour `t` is `(t-1h,t]`; run-zero nulls remain missing.
Missing WMO code is not clear weather. No empirical distribution is produced by
these deterministic fields.

**Observed documentation mismatch:** the first probe passed `start_hour` and
`end_hour`; the API returned HTTP 400 with reason
`Parameter 'start_hour' must not be set`. A second request without those
parameters returned the default 168 hours (10,392 bytes). Adding
`forecast_days=10` returned 240 hours. The generic documentation's broad statement
that forecast parameters are accepted must not be used to implement bounded
upstream time slicing. Fetch a capped location/run product and bound reader
windows locally.

Local captured evidence:

- `.omc/research/weather-single-run-probe-2026-09-12.json` and its receipt:
  provider's 400 response.
- `.omc/research/weather-single-run-full-probe-2026-09-12.json` and its receipt:
  seven-day successful response.
- `.omc/research/weather-single-run-10day-probe-2026-09-12.json` and its receipt:
  ten-day successful response.
- Ten-day saved-file SHA-256:
  `34812e5101395d607ca9c61454a0aee140dbc5628a052b2b276fc85f01e45186`.
  This hashes the local UTF-8 capture including its writer-added newline,
  not an asserted checksum of the transport bytes.
- `.omc/research/weather-provider-{docs,details,selector}-2026-09-12.md`:
  captured official documentation with source URLs and source line references.

The local research directory is ignored by Git. The successful ten-day response
and receipt are also retained under
`services/agri-data-service/tests/fixtures/weather_forecast/` as
`ecmwf_ifs_20260908T0000_boise.json` and its `.receipt.json` companion.
The receipt records both the 14,575-byte saved-file hash above and the
14,573-byte response-text hash
`6909f2b34b420fa34c4bd66c4e812e4f605b564dd7c3751139b570a33593297d`.
PowerShell appended CRLF when saving; the fixture test explicitly slices the
recorded response length and verifies both hashes before normalization.

## Local implementation following reconciliation

`pipeline/direct/weather_forecast/source.py` now provides a bounded public
Single Runs fetch and strict normalizer into the new forecast warehouse
contracts. It accepts only the probed 00 UTC cycle, one location, this exact
eleven-variable request and its thirteen-variable output inventory including
wind u/v. It caps source bytes at 1 MiB and the complete fetch at fifteen seconds.
Hourly windows are trimmed by downstream readers; upstream `start_hour` is not
sent. The source adapter preserves the request coordinates in the source URL and
the returned coordinates in every sample row. It retains lead-zero missing
precipitation/gust intervals and never publishes them as pre-initialization
accumulations. Both wind components are missing when either source wind input
is absent.

The normalizer requires exact request/run/hash provenance and the attribution
string `CC-BY-4.0; attribution: Open-Meteo / ECMWF`. It refuses nonnull
`provider_issued_at` and `model_version`: the JSON does not contain those fields,
and this adapter has no separate run-matched evidence descriptor. Documented
cycle information above remains external evidence, not a verified JSON field.
The shared bounded HTTP response does not expose Retry-After, so durable
provider cooldown preservation remains blocked on the HTTP ownership handoff.
Tests were authored against the real fixture and adversarial changes but not run
in this lane; the parent owns the final integrated sweep and independent review.

## Licence, quota and missingness

[Open-Meteo's licence page](https://open-meteo.com/en/licence) specifies CC BY 4.0
for API data. Preserve attribution and identify PlantGeo transformations; source
data licensing and hosted API access entitlement are distinct.

The [current pricing table](https://open-meteo.com/en/pricing) lists free access
as noncommercial, with 600/minute, 5,000/hour, 10,000/day and 300,000/month
limits. Single Runs is included in free, Professional and Enterprise, and excluded
from Standard. Professional lists five million monthly calls. These documented
limits are not verification of this project's purchased entitlement. The old
“$99 paid tier” authorization does not identify today's account tier or prove
access to a customer Single Runs host. No account, key or billing state was read.

The probe establishes real JSON nulls, including valid run-zero missing values.
It does not establish every variable/domain/version failure mode. Admission must
reject nonfinite/unexpected-unit/invalid-axis content, preserve nulls with a
source-missing state, and distinguish absence from zero. A missing initialization,
provider failure, domain refusal and not-yet-generated hour must remain separate
states; an unclassified HTTP error must not be guessed into one of them.
Precipitation probability is **excluded**: the general API documents an ensemble
basis, and no independent uncertainty product/run was admitted here.

## Admission decision and remaining gates

**A sampled deterministic candidate is source-probed; production admission stays
open.** Freeze the candidate to this exact endpoint/model and tested variables,
with 240-hour ceilings, before implementing against it. The following remain
necessary:

1. Independently review and retain the real fixture, derive interval and wind
   semantics, validate elapsed hours and daily timezone windows, and establish
   replay with one pinned run. Verify nonzero initialization cycles separately;
   this probe proves only the 00 UTC case.
2. Admit sampled support only. Nominal 9 km describes the source; it does not
   authorize a continuous raster, a square around each point, or an interpolated
   field. Native extraction or a separately governed derived-field method,
   support mask and validation evidence are still required.
3. Establish an operational discovery path and run-matched release provenance
   where available, measured byte/row/request ceilings, strict retention,
   source/artifact completeness, publication CAS and recovery.
4. Resolve production API entitlement at release time, retain durable
   Retry-After/cooldown handling, and register forward, repair and status duties
   only after the shared owners transfer their files.
5. Keep ensemble staged code separate until exact model/member numbering,
   run-pinning, support and probability/quantile semantics are re-probed and
   governed. Do not infer native support from its existing product constants.

No Railway access, deployment, production publication, external write or shared
runtime edit was performed for this reconciliation. This document is ready for
independent source-contract review; neither weather track is archive-ready on
this evidence alone.
