---
type: source-admission
track: weather_forecast_parquet_lane_20260911
recorded_on: 2026-09-12
status: fixture_only_real_source_not_admitted
---

# Source decision for the local vertical slice

The current task explicitly authorizes a deterministic fixture adapter when real
source admission cannot be completed. This slice admits **only a synthetic local
test product**. It does not populate the Open-Meteo product or close F0. The
forecast-specific track specification governs provider forecasts; the older
Monte Carlo 30-day/default provenance convention does not turn this deterministic
fixture into an ensemble, an observation, or an admitted provider product.

## Real-source investigation

Open-Meteo's Single Runs API accepts a UTC initialization selector and preserves
individual runs, unlike the stitched operational forecast series. Initialization
is distinct from public release time. This is the candidate endpoint for later
admission, not the current-condition polling adapter.
[Provider documentation](https://open-meteo.com/en/docs/single-runs-api).

A bounded read-only probe on 2026-09-12 requested
`https://single-runs-api.open-meteo.com/v1/forecast` with
`latitude=40`, `longitude=-105`, `models=gfs_global`,
`run=2026-09-11T00:00`, `hourly=temperature_2m`, `forecast_days=1`, `timezone=UTC`.
The browser tool returned an internal error; a direct request initially hit the
sandbox socket restriction, then succeeded with HTTP 200 after read-only network
authorization. **Network access is not the remaining admission blocker.**

The response contains 24 hourly temperatures for September 11 in degrees Celsius,
returned coordinates `(40.006516, -105.0)`, GMT/zero offset, and elevation 1592 m.
It does not echo a model version, run identifier, or provider issue/release time.
`generationtime_ms` is response computation duration, not issue time. Raw response
SHA-256 is `099d00cf256302968ba9461323a3448d9e6a027134572e143072ae935ca0d8f5`;
the [immutable probe evidence](source-probe-20260912.md) preserves the response.
The original local capture is `.omc/research/forecast-single-run-probe-2026-09-12.json`.
No values from that probe are published by the fixture writer.

| Admission dimension | Candidate status / exact next evidence |
| --- | --- |
| Provider/product | Open-Meteo Single Runs, explicit `gfs_global`; one bounded endpoint response measured. |
| Model/version/run | Requested initialization known; freeze the provider's exact model/version mapping and provenance rule before production admission. Do not infer an echoed run from its absence. |
| Provider issue/release | Not supplied in the probe; preserve explicit unavailable provenance, and obtain documented publication/discovery semantics. |
| Cadence/horizon | Probe establishes only 24 returned hours, not a maximum horizon or reliable refresh cadence. Measure and freeze the exact product schedule and complete supported lead range. |
| Support | Returned model sample differs from requested point. No native cell polygon or interpolation support is established. Freeze selection/downscaling and distance rules before publication. |
| Variables/units | Temperature probe only. Humidity/cloud/precipitation/wind must be reconciled with each response's unit and variable catalogue. |
| Missingness | Temperature probe contains no nulls; it proves no general missing-value, out-of-domain, stale-run, or unavailable-run contract. Obtain fixtures for those outcomes. |
| Licence/quotas | Provider terms distinguish free non-commercial API access from commercial service access. Data attribution and account entitlement must be recorded for the intended deployment; this task acquires no subscription. |

Open-Meteo documents humidity/cloud as percentages, precipitation as a preceding-hour
sum in mm, and precipitation probability as an ensemble-derived hourly probability.
These are candidate mappings only. The fixture's following-hour accumulation
convention must never be applied to Open-Meteo without conversion. Deterministic
temperature does not justify inventing precipitation probability or confidence
intervals. [Variable definitions](https://open-meteo.com/en/docs).
Access and attribution requirements are recorded for later source review under
the [provider terms](https://open-meteo.com/en/terms).

The old `ingest/open_meteo_ensemble.py` has an explicit model request and ensemble
parsing; `execution/ensemble_forecast.py` derives issue time from `issue_date` at
midnight and acknowledges that the endpoint answers current runs. Its synthetic
receipts and persistence scaffolding establish neither this run identity nor
governed forecast publication. They are evidence to reuse cautiously, not a
PostgreSQL migration dependency or a fallback.

## Frozen synthetic fixture contract

| Dimension | Local contract |
| --- | --- |
| Identity | `weather-forecast/v1`; provider `PlantGeo fixture`; model `deterministic-fixture-v1`; run `fixture-20260912T000000Z-v1`. |
| Source/licence | Deterministically generated local test values, CC0; no provider API fetch or attribution substitution. |
| Initialization/lifecycle | Explicit synthetic clock at `2026-09-12T00:00:00Z`, labelled `synthetic_fixture_clock`; issue/fetch/admission/publication fields never assert real operational events. |
| Cadence/horizon | Manual fixture publication only; 72 hourly valid instants, September 12 00:00 inclusive to September 15 00:00 exclusive. No history product. |
| Support | Four exact samples `(40,-105)`, `(40.1,-105)`, `(40,-104.9)`, `(40.1,-104.9)`; no snapping, interpolation, source-grid footprint or continuous field. |
| Variables | Temperature degC; relative humidity/cloud cover percent; precipitation mm; wind eastward/northward/speed m/s and meteorological direction degrees. |
| Intervals | Synthetic precipitation represents `[valid_time, valid_time + 1 hour)`; instantaneous variables bind the valid instant. UTC is the stored and initial presentation timezone. |
| Wind | Earth-relative 10 m components; from-direction clockwise from true north; `u=-speed*sin(direction)`, `v=-speed*cos(direction)`. Component means precede derived magnitude/direction; calm direction is explicitly absent. |
| Missingness | First sample at lead hour 6 has a deliberate cloud gap; nulls carry a reason. Numeric zero precipitation remains a value. |
| Uncertainty | Deterministic fixture only; no probabilities, members, quantiles or calibrated confidence. |
| Ceilings | 72-hour artifact horizon, 48-hour location request, four points, 2,304 long-form rows; Python 2 MiB metadata/artifact/response and TypeScript 256 KiB response limits are local design caps, not measured provider budgets. |

Separate review and final check evidence determine local acceptance. Real provider
admission, shared registry/availability/executor transfers, real source-to-artifact
conservation, scheduled recovery, and production authorization remain merge and
release gates for the real forecast product.
