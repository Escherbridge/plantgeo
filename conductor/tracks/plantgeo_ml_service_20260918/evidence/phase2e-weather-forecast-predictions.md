---
type: evidence
track: plantgeo_ml_service_20260918
slice: p2e-weather-forecast
recorded_on: 2026-09-19
status: authored-unverified
---

# Phase 2E, weather-forecast (FR-12): what was built, and what the sweep will say

Author slice. No pytest, ruff or mypy was run here (owner rule 2026-08-25: authors predict, a
separate monitor judges). What IS run below is smoke execution of the new code paths in a plain
interpreter, reported as such.

## 1. Files created

| file | what it is |
|---|---|
| `services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/weather_forecast.py` | the frozen 19-column stream schema, the ONE variable catalogue, the missing-reason and support vocabularies, and the registration that publishes the schema into `warehouse/streams.py`'s observed registry |
| `services/plantgeo-ml-service/src/plantgeo_ml_service/pipeline/sources/__init__.py` | package docstring: the only HTTP in this service |
| `services/plantgeo-ml-service/src/plantgeo_ml_service/pipeline/sources/AGENTS.md` | rationale: the three corrections on lift, why `single-runs-api`, the wind formula, calm, the fetch budget, the source receipt |
| `services/plantgeo-ml-service/src/plantgeo_ml_service/pipeline/sources/protocol.py` | `WeatherForecastSource` and its payload protocols, `SourceBounds`, `SourceCoverage`, `SourceReceipt`, the four error types |
| `services/plantgeo-ml-service/src/plantgeo_ml_service/pipeline/sources/open_meteo.py` | the bounded single-run fetch, the URL builder, the verified-pairing parser, the wind derivation, the 429 refusal, credential redaction |
| `services/plantgeo-ml-service/src/plantgeo_ml_service/pipeline/weather_forecast_daily.py` | `run_weather_forecast_daily(store, issue_date, cells, *, dry_run_prefix=None, ...)`: fetch, reshape, write all four rungs, completion markers, evidence objects, availability generation and pointer |
| `services/plantgeo-ml-service/tests/test_open_meteo_source.py` | 16 cases over the replayed probe: catalogue identity, probe-exact URL, pairing, precipitation START labelling, no negative lead, calm, receipt, seven refusal cases, rate limit, byte ceiling, replay byte-identity, non-JSON |
| `services/plantgeo-ml-service/tests/test_weather_forecast_daily.py` | 12 cases: four rungs written and completed, `kind=observed` day grammar, lead/absence invariants, coarse merge with re-derived bearing, byte-identical partitions, publication with pointer and receipts, dry-run containment, refusals |
| `services/plantgeo-ml-service/tests/fixtures/open_meteo/*` | four verbatim captures from `.omc/research/forecast-s3-probe-20260919/` (two responses, two request URLs) |

## 2. Out-of-ownership edits made, and why

1. **`warehouse/streams.py`, one line at the bottom** (the single permitted import re-export):

       from plantgeo_ml_service.warehouse import weather_forecast  # noqa: E402, F401

   Strictly needed: `ObjectStore.write_partition` resolves a stream's contract through
   `streams.stream_schema(layer, kind)` and refuses a caller-supplied schema, so a lane whose schema
   is not in `OBSERVED_STREAM_SCHEMAS` cannot write a partition at all. `weather_forecast.py`
   registers itself (idempotently, refusing a different contract under the same name) and this line
   is what makes importing `streams` enough to see it.

2. **`pyproject.toml` + `uv.lock`**: `httpx>=0.27` added to `[project].dependencies`, `uv lock`
   re-run (two lines changed in the lock: the dependency and its `requires-dist` entry; no version
   moved). `httpx` was already resolved into the venv transitively, so no download happened.

## 3. Requests for changes this slice must NOT make

- **`warehouse/lanes.py` needs a `weather-forecast` clock entry.** This slice does not edit it. The
  values needed:

      LaneContract(
          slug="weather-forecast",
          history_floor=date(2026, 9, 18),   # the earliest run this platform has fetched
          publication_lag_days=0,            # the issue day's 00Z run is readable the same day
          nature="release_series",
          cadence_days=1,
          forecast_module=None,              # provider runs are deterministic, no ML forecaster
      )

  Nothing in this slice reads `lane_contract("weather-forecast")`, so its absence is not a runtime
  fault today; a feature builder that later wants a leakage guard for this lane needs it.
- **agri-data-service lane registry**: the `weather-forecast` slug with `forecast_module=None`, per
  FR-12, sequenced with FR-5a. Not this slice.
- **`docs/lanes/weather-forecast.md`** and the RUNBOOK weather-forecast row: not this slice.
- **`tests/test_streams.py:99`** pins the registered stream names and must gain `"weather-forecast"`
  (see section 5).

## 4. Smoke evidence (interpreter, not pytest)

Against the captured probe response, with an in-memory bucket and pointer store:

- every module under `src/plantgeo_ml_service/**` imports; `registered_stream_names()` returns the
  eight streams including `weather-forecast`.
- `forecast_run_url(...)` reproduces `multi-location-request.txt` byte for byte.
- the wet crosscheck confirms the labelling: hours `00:00..23:00` of 2026-09-19 at `(5.45, 100.3)`
  sum to 17.8 mm, which is the provider's own `precipitation_sum` for that day. No shift is applied.
- one daily run over two cells wrote 768 base rows, four rungs (z00/z05/z09/z13), fifteen objects,
  `lead_hours` 0..47 with zero negatives, zero rows where a null value lacked a `missing_reason`,
  publication outcome `advanced`, one source evidence object and four terminal evidence objects.
- the merge case (two cells inside one z5 lattice cell) produced 384 coarse rows with `cell_id`
  null, one coordinate pair, and `wind_speed_10m`/`wind_direction_10m` equal to the values derived
  from the MEAN `wind_u_10m`/`wind_v_10m` to within 1e-9.
- two runs with the same clock produced identical part digests and an identical generation digest.
- a response paired against a reversed request list is refused.

## 5. Predicted sweep failures

1. **`tests/test_streams.py::test_every_pinned_stream_is_named_once` WILL FAIL.** It pins the seven
   previous names; the registry now returns eight. The fix is one line, in a file this slice does
   not own.
2. **A `lanes` parity or coverage test naming every registered stream** may fail for the same
   reason. `tests/parity_parquet_cases.py`'s `PARITY_STREAMS` is an explicit list, so the parity
   suite itself should be unaffected.
3. **mypy strict** is the most likely remaining noise, in three places: `pa.Table.from_pylist` with
   `list[dict[str, object]]`, the `WeatherForecastRun` returned by the concrete source against the
   `WeatherForecastRunPayload` protocol the lane declares, and `AvailabilityRow(nature=...)`
   accepting `WEATHER_FORECAST_NATURE` as a `Literal`. All three are annotation shapes, not logic.
4. **ruff**: `PLR0913` exemptions are already written on the four functions over five parameters.
   Residual risk is `PLR2004` in the two new test files; every numeric comparison there was hoisted
   to a named constant, but the rule is easy to trip and worth a first-run check.
5. The QUALITY receipt must be regenerated after the sweep; this slice does not touch it.

## 6. Decisions a reviewer should challenge if they disagree

- **The pairing rule is "no beneficial swap", not "closest to its own request".** The strict rule
  fails on a correct response whenever two requested cells share one provider grid point at unequal
  distances, which is the normal case for a lattice finer than 0.25 degrees. Proven by the probe: a
  strict argmin refused the shared-grid-point replay.
- **A run with no pointer store is refused before it writes.** Writing a partition does not publish
  it (FR-4a), so a lane that could write without publishing would create days nobody can select.
- **Coarse rungs average intensive variables and re-derive wind from mean components.** Precipitation
  is averaged rather than summed: it is a depth, and summing depths across merged cells would
  invent rainfall.
- **The lane refuses cells outside the source's coverage** rather than publishing `outside_domain`
  rows. The reason is that no hours are known for a cell that was never fetched, so the absence
  would have no valid-time axis to hang on.
