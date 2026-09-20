# `ingest/weather_forecast` — the weather-forecast layer's source domain

W8-E S2 (`.omc/ultrapilot-20260918/W8-E-PLAN.md` §2 row S2): the source protocol and bounded
Open-Meteo run fetch, one layer up from `pipeline/direct/weather_forecast/` (S3, not yet built).
Nothing here writes Parquet, touches `lane_registry.py`, or binds a region manifest -- see the
plan's "OWNED FILES" list. This package is a `ingest/` **domain** package
(`tests/test_layer_import_contract.py::test_domain_packages_do_not_import_each_other`): it may
import flat `ingest/` modules (`http.py`, `open_meteo.py`, `policy.py`) freely, but nothing here may
ever import another domain subpackage under `ingest/`, and none may import this one.

## The source protocol

`source_protocol.py` declares `WeatherForecastSource`/`WeatherForecastRunPayload`/
`WeatherForecastLocationPayload`/`WeatherForecastSamplePayload`, modelled directly on
`pipeline/direct/drought/source_protocol.py`'s `DroughtSource`/`DroughtReleasePayload` pair. The one
deliberate difference: this protocol's `fetch_run` carries no `retry_attempts`/`retry_base_seconds`/
`retry_max_seconds` knobs. `DroughtSource.fetch_release_day` retries a whole fetch-and-parse unit
with jittered backoff (`pipeline/direct/drought/usdm.py::_retry_async`); this layer's S2 acceptance
is explicitly the opposite -- "rate-limit raises `OpenMeteoRateLimitError`
(`ingest/open_meteo.py:128`) not a retry loop" -- so there is nothing to retry around. A transport
fault (a dropped connection, a timeout) is still retried, but only inside `ingest.http.fetch_bounded`
itself, and never a status code; this matches `ingest/open_meteo.py::fetch_archive_daily`'s existing
contract exactly, which this module's `fetch_forecast_run_text` was written as a sibling of.

## Why `single-runs-api`, not `api.open-meteo.com`

Three Open-Meteo hosts now exist in this codebase, and they answer three different questions:

| host | question it answers | module |
|---|---|---|
| `api.open-meteo.com` | "what is happening right now" | `ingest/open_meteo.py`'s current-conditions poller |
| `archive-api.open-meteo.com` | "what did ERA5(-Land) reanalyze for this settled past day" | `ingest/open_meteo.py`'s archive endpoint |
| `single-runs-api.open-meteo.com` | "what did model run `X` initialized at `T` predict" | this module |

The standard forecast endpoint (`api.open-meteo.com`) silently stitches the newest available run
into one rolling series and does not let a caller pin a past initialization; only the Single Runs
endpoint preserves one model's one initialization as an addressable object
(`conductor/tracks/weather_forecast_parquet_lane_20260911/evidence/source-admission.md`, "Real-source
investigation"). `warehouse/schemas/weather_forecast.py`'s `run_id` column exists specifically to
pin one published series (ML spec.md "Readers pin one published run"), which the stitched endpoint
cannot honestly support.

## What is measured, and what is an assumption

A read-only probe on 2026-09-12 (`source-probe-20260912.md`, SHA-256 recorded there) hit
`single-runs-api.open-meteo.com/v1/forecast` with `models=gfs_global`, `run=2026-09-11T00:00`, one
point, `hourly=temperature_2m`, and got HTTP 200 back with a single-location JSON object (not an
array). That is the ONLY thing about this endpoint's shape this branch has actually observed.
Everything else this module assumes is carried over from the STANDARD forecast/archive endpoints'
documented and measured shape, and is flagged here rather than silently trusted:

- **Multi-location response shape.** `parse_forecast_run_payload` accepts a JSON array with the
  first entry's `location_id` omitted and the rest numbered from 1
  (`execution/open_meteo_lane.py::ordered_locations`'s proven contract for the archive endpoint), or
  a bare object for a single coordinate. Single Runs has never been probed with more than one point
  requested. **Unverified against a live multi-day, multi-location response** -- the first S3 fetch
  against real coordinates should record a new probe evidence file before this assumption is
  trusted for production.
- **`forecast_days` maximum.** `MAX_FORECAST_RUN_DAYS = 16` is read from the endpoint's own
  documentation (open-meteo.com/en/docs/single-runs-api, read 2026-09-18), matching
  `OPEN_METEO_FORECAST_PAST_DAYS_MAXIMUM`'s existing "documentation-sourced, NOT live-probed"
  convention (`ingest/open_meteo.py:66-70`). Not live-probed here either.
- **`provider_issue_time` is always `None`.** The probe's response echoed no model version, run
  identifier, or provider issue/release time (`source-admission.md`, "does not echo ... provider
  issue/release time"). `generationtime_ms` is response computation duration, not issue time, and is
  not read at all.

## The precipitation window

Open-Meteo documents `precipitation` as a PRECEDING-hour accumulation: the value at hourly index `t`
sums rainfall over `[t-1h, t)`. `warehouse/schemas/weather_forecast.py`'s frozen `VARIABLES` table
(S1) names the platform's own convention `"sum_over_following_hour"` -- meaning THIS layer's
`valid_time` is defined as the window's START, with `interval_end = valid_time + 1h`. Those two
conventions describe the SAME physical window from opposite ends, and applying one where the other
is meant silently shifts every precipitation reading by exactly one hour -- the codex branch's own
evidence names this trap explicitly: "The fixture's following-hour accumulation convention must
never be applied to Open-Meteo without conversion"
(`weather_forecast_parquet_lane_20260911/evidence/source-admission.md`).

`_precipitation_sample` performs the conversion at the source boundary, per `federation.md` §2
("units, datums and calendars normalize at the source boundary"): Open-Meteo's hourly timestamp `t`
becomes this row's `interval_end`, and `valid_time = interval_start = t - 1 hour`. Every OTHER
variable this module emits is instantaneous and keeps `valid_time = t` unshifted.

## Wind: derived from speed/direction

Open-Meteo's hourly catalogue publishes `wind_speed_10m`/`wind_direction_10m` (confirmed by the
existing `CURRENT_VALUE_BOUNDS` table for the current-conditions endpoint,
`ingest/open_meteo.py:79-85`), not `wind_u_10m`/`wind_v_10m` directly. The frozen schema's grain
requires the u/v pair as the PRIMARY published rows (`statistic="instantaneous_earth_relative"`)
with speed/direction as DERIVED rows (`statistic="derived_vector_magnitude"` /
`"meteorological_from_true_north"`) -- see `.omc/ultrapilot-20260918/W8-E-PLAN.md` §2's "Wind
fetched as u/v components or converted deterministically from speed/direction with the formula
named in AGENTS.md", which permits either sourcing method as long as the formula is named. This
module converts:

```
u = -speed * sin(radians(direction))
v = -speed * cos(radians(direction))
```

where `direction` is the METEOROLOGICAL "from" bearing, clockwise from true north (Open-Meteo's own
convention). This is the same formula the codex fixture's frozen contract already used
(`weather_forecast_parquet_lane_20260911/evidence/source-admission.md`, "Frozen synthetic fixture
contract", row "Wind": `u=-speed*sin(direction), v=-speed*cos(direction)`), carried over here as
prior art -- not re-derived independently, and not itself live-probed against a real Open-Meteo wind
response.

**Calm has no defined bearing.** When `speed == 0.0`, `wind_direction_10m`, `wind_u_10m` and
`wind_v_10m` all become a governed absence (`missing_reason = "not_generated"`) even if the provider
happened to supply a (physically meaningless) direction reading at calm; `wind_speed_10m` itself
stays the real, valid `0.0` -- a numeric zero is a value, never a stand-in for absence
(`warehouse/schemas/weather_forecast.py:18-20`). This mirrors the codex fixture's own stated
contract ("calm direction is explicitly absent").

Round-tripping matters: because `u`/`v` are derived from the SAME single-point reading `speed`/
`direction` (never averaged across points -- there is exactly one point per request here), converting
back with `atan2` and `hypot` reproduces the original speed/direction exactly. The "scalar direction
averages are forbidden" rule the plan states exists for a DIFFERENT step -- spatially aggregating
many lattice cells into a coarse rung (S3's rung rollup) -- and does not apply at this fetch-time
single-point conversion at all.

## Bounded fetch budget (S2)

Frozen in `.omc/ultrapilot-20260918/W8-E-PLAN.md` §2, "Bounded fetch budget (S2)":

- `WEATHER_FORECAST_RUN_BOUNDS = UpstreamBounds(max_bytes=64 MiB, timeout_seconds=120.0)` for one
  run pull -- a different shape from the 128 KiB/5 s current-conditions poll
  (`ingest/open_meteo.py:63`).
- `MAX_FORECAST_RUN_LOCATIONS = MAX_ARCHIVE_LOCATIONS_PER_REQUEST` (`ingest/open_meteo.py:126`,
  reused, not redeclared: 200).
- One run per invocation -- structural, not a runtime check: `fetch_forecast_run` takes exactly one
  `model_init_time`, never a list of them.
- `MAX_FORECAST_RUNS_PER_DAY = 4` is published as a budget constant for a future S3 executor to
  schedule against. `ingest/` owns no clock, cron, or call counter, so this module cannot enforce a
  daily ceiling itself -- doing so would mean this stateless fetch adapter silently gained hidden
  state, which is a bigger contract change than S2 owns.
- `_require_run_request` REFUSES (raises `WeatherForecastRequestError`) a request whose coordinate
  count, variable list, or `forecast_days` exceeds its budget, rather than truncating it silently --
  the plan's explicit acceptance wording for this row.
- **Retry-After as a durable cooldown, not a sleep.** The plan says a 429's `Retry-After` should be
  "persisted as durable cooldown into the work planner rather than slept on". This module raises
  `OpenMeteoRateLimitError` with the quota SCOPE it classified (`"day"`/`"hour"`/`"minute"`/
  `"unknown"`) from the response BODY, reusing `ingest/open_meteo.py`'s own
  `RATE_LIMIT_SCOPE_MARKERS` table -- `ingest.http.BoundedResponse` does not expose response
  HEADERS at all (only `status`/`content_type`/`text`/`payload_error`/`byte_count`), so the literal
  `Retry-After` header value is not readable through the shared bounded-fetch primitive without
  changing `http.py`, which is outside this slice's owned files. Persisting a durable cooldown from
  the classified scope (rather than the header's exact seconds) is therefore explicitly left to S3's
  `forward.py`/job-planner integration, which is where the plan's "work planner" lives.

## Wave-2 (S2) file inventory

- `source_protocol.py` -- the layer's `runtime_checkable` Protocols.
- `open_meteo.py` -- `OpenMeteoWeatherForecastSource`, the bounded URL builder, fetch, and parser.
- `tests/ingest/test_weather_forecast_open_meteo.py` -- URL/budget/parse/wind/precipitation/rate-limit
  unit tests plus a byte-identical replay test against a recorded fixture
  (`tests/fixtures/weather_forecast_open_meteo_run_response.json`).
