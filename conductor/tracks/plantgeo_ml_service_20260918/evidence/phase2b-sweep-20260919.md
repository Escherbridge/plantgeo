---
type: track-evidence
track: plantgeo_ml_service_20260918
slice: monitor-sweep
authored_on: 2026-09-19
status: swept
---

# Phase 2B/2E monitor sweep — fire-risk, AnEn/Monte Carlo/forecast-lane-bootstrap, weather-forecast

Monitor pass over the three unverified author slices (`phase2b-fire-risk-predictions.md`,
`phase2b-knn-predictions.md`, `phase2e-weather-forecast-predictions.md`). All edits below are under
`services/plantgeo-ml-service/`. No git commands were run.

## Step 0 — cross-slice requests only the monitor could act on

**(a) `weather-forecast` lane clock.** Added to `src/plantgeo_ml_service/warehouse/lanes.py`
`LANE_CONTRACTS` exactly per phase2e's evidence: `history_floor=date(2026,9,18)`,
`publication_lag_days=0`, `nature="release_series"`, `cadence_days=1`, `forecast_module=None`. It is
ORIGINATED (no sibling counterpart — the sibling deleted its own weather-forecast lane), so it is
**excluded** from `tests/parity_parquet_cases.py::PARITY_LANES` (still `= PARITY_STREAMS`, unchanged)
and documented inline with a comment naming why. `tests/test_streams.py::test_every_pinned_stream_is_named_once`
(line 99 region) updated to include `"weather-forecast"` in its sorted tuple, since
`registered_stream_names()` now returns eight names via `weather_forecast.py`'s self-registration.

**(b) Mechanical requests found in the three evidence files:** none. All numbered requests in
`phase2b-fire-risk-predictions.md` (§"Requests for changes...", items 1–8), `phase2b-knn-predictions.md`
(R1–R6) and `phase2e-weather-forecast-predictions.md` (§3) are either coordinator-owned document merges,
need lane-contract VALUES this monitor was not given (fire-risk's own lane, sensors/water-gauges), or
are design decisions (row-select strategy for `forecast_lane_bootstrap`, `lane_identity_for` taking
`nature`, a cell dimension for fire-risk). None was a bare re-export/`__all__`/fixture-path fix. Left
untouched.

## Step 1 — commands and raw counts

- `uv sync --locked --all-extras`: succeeded against the existing lock (phase2e's `httpx` addition was
  already locked).
- `uv run --no-sync ruff format --check src tests scripts`: 13 files would reformat.
- `uv run --no-sync ruff check src tests scripts`: 38 errors (PLR2004 ×12, PLR0913 ×10, TC003 ×7,
  ARG002 ×2, TC002 ×1, RET504 ×1, UP012 ×1, ARG001 ×1). All confined to the three slices' new files
  (verified by listing unique `-->` paths — zero pre-existing files implicated).
- `uv run --no-sync mypy src scripts`: 24 errors in 6 files (`fire_risk_model.py`, `fire_risk_features.py`,
  `covariate_vectors.py`, `fire_risk_backtest.py`, `monte_carlo_daily.py`, `weather_forecast_daily.py`).
- `uv run --no-sync pytest -q`: 4 failed, 485 passed (pre-fix).

## Step 2 — fixes applied (one batch), re-swept once

### mypy (root cause fixed once, propagated)
- `src/plantgeo_ml_service/foundation/parquet_paths.py:26` — `BASE_PARTITION_ZOOM: Final[int]` →
  `Final[ZoomTier]`. This one annotation fix cleared 3 of the 24 mypy errors on its own (the `zoom:
  ZoomTier = BASE_PARTITION_ZOOM` defaults in `fire_risk_features.py`, `covariate_vectors.py`,
  `monte_carlo_daily.py` all previously widened to plain `int`).
- `fire_risk_features.py:508` — `schema: dict[str, pl.DataType]` → `dict[str, type[pl.DataType]]`
  (polars dtype classes, not instances).
- `monte_carlo_daily.py` — cast `signal_name`/`longitude`/`latitude` to their real runtime types right
  after the `_grouped()` unpack (which is typed `tuple[object, ...]` by design), instead of at every
  call site.
- `fire_risk_model.py:_sigmoid`, `covariate_vectors.py:query_vector` — `cast("np.ndarray", ...)` on the
  return, with a one-line comment: numpy's stubs resolve arithmetic on an unparameterized `ndarray` to
  `Any`.
- `fire_risk_backtest.py:climatology_rate` — `# type: ignore[arg-type]  # float64 column` on two
  `float(Series.mean() or 0.0)` calls (`Series.mean()`'s stub return type is a wide dtype union).
- `covariate_vectors.py:CovariateMatrix.query_vector`'s callers — same ignore convention on
  `float(_single_cell_identity(...))`, since that helper is typed `object` to also serve `cell_id`.
- `pipeline/sources/protocol.py::WeatherForecastSource` — converted `source_slug`/`coverage`/
  `max_locations_per_request` from plain mutable Protocol attributes to `@property` stubs, and
  `pipeline/sources/open_meteo.py:472` annotated `OPEN_METEO_WEATHER_FORECAST_SOURCE:
  Final[WeatherForecastSource]`. Root cause: mypy treats a frozen dataclass's fields as read-only, so a
  mutable-attribute Protocol can't match it; this surfaced only once the constant's declared type
  stopped being inferred as the concrete dataclass (needed for the `_validated_cells`/`_fetch_every_batch`
  union-arg-type errors on `weather_forecast_daily.py:171,174`).
- Result: `mypy src scripts` → **0 errors**.

### ruff
- `ruff format` applied (13 files, purely the authors' own unformatted lines — long dict/assert
  literals).
- `--fix` cleared the one safe UP012 (`.encode("utf-8")` → `.encode()`).
- `PLR0913` (10 sites, `analog_ensemble_daily.py`, `forecast_lane_bootstrap.py` ×4,
  `monte_carlo_daily.py` ×3, `test_covariate_vectors.py`): added `# noqa: PLR0913 - one keyword per
  <boundary> is the contract`, matching the exact convention already used in
  `covariate_wind_model.py`, `recommendation_models.py`, `object_store.py`, etc.
- `TC003`/`TC002` (8 sites across `fire_risk_backtest.py`, `fire_risk_daily.py`,
  `test_analog_ensemble_daily.py`, `test_fire_risk_daily.py`, `test_forecast_lane_bootstrap.py`,
  `test_monte_carlo_daily.py`): moved annotation-only `date`/`datetime`/`Path`/`pyarrow` imports into
  `TYPE_CHECKING` blocks — **after verifying no runtime use** in each file (`test_fire_risk_daily.py`'s
  `Path` genuinely IS used at runtime for `FIXTURES = Path(__file__)...` and was correctly left as a
  normal import; only `pa` moved there).
- `PLR2004` (12 sites): hoisted every flagged literal to a named module- or function-local constant
  (`FEATURE_MATRIX_NDIM`, `SHA256_HEX_LENGTH`, `DEFAULT_CELL_LONGITUDE/LATITUDE`,
  `THREE_COMPLETE_DAY_COUNT`, `HELD_OUT_FOLD_COUNT = len(YEARS) - 1`, `ZOOM_TIERS[-1]`/`ZOOM_TIERS[:-1]`
  in place of bare `13`/`(0,5,9)`, `len(horizons)`, `class_size`, `RANDOM_SEED`,
  `merged_base_cell_count`).
- `ARG002` (2, `test_fire_risk_features.py::FakeReader.read_lane_window`): `# noqa: ARG002` per param,
  matching the existing `seasonal_candidates.py`/`test_availability_publisher.py` convention (fake
  implements the `ObservedReader` protocol; `zoom`/`as_of` are unused by design).
- `ARG001` (`test_monte_carlo_daily.py::test_a_whole_turn_is_bounded_by_the_registry`): the `tmp_path`
  fixture param was genuinely unused (no I/O in the test) — removed rather than suppressed.
- `RET504` (`analog_ensemble_daily.py:_publish_rows` call site): `receipt = _publish_rows(...); return
  receipt` → `return _publish_rows(...)`.
- Result: `ruff format --check` and `ruff check` → **all clean**.

### pytest (4 real failures, 3 fixed, 1 left standing)
1. `test_fire_risk_daily.py::test_every_written_row_carries_the_six_provenance_columns` — read the base
   rung straight off `target` (no dry-run prefix applied), while `run()` defaults
   `dry_run_prefix=SCRATCH_PREFIX`. Fixed by wrapping the read in `ObjectStore(backend=target.backend,
   prefix=SCRATCH_PREFIX)`, the exact pattern the file's own `_base_table()` helper (line 263) already
   uses.
2. `test_fire_risk_daily.py::test_the_prediction_receipt_is_written_and_a_replay_adopts_it` — same bug:
   `target.read_object(first.relative_path)` missed the scratch prefix the receipt was actually written
   under. Same fix.
3. `test_monte_carlo_daily.py::test_the_receipt_states_which_forecaster_ran_and_what_it_refused` —
   asserted `wire["lane"]`; `LaneForecastReceipt.to_wire()` (monte_carlo_daily.py:125) names the field
   `"layer"` everywhere, matching the rest of the codebase's `layer=` convention. Test corrected to
   `wire["layer"]`; same value, correct key, not a weakened assertion.
4. **Standing failure, not fixed:**
   `test_monte_carlo_daily.py::test_every_registry_stem_is_in_the_explicit_dispatch_map` —
   `load_forecast_module("signal").METHOD_NAME` raises `AttributeError`.
   `method/monte_carlo/signal.py` predates this test's convention and exports two names,
   `METHOD_NAME_ADDITIVE_ANOMALY` / `METHOD_NAME_EMPIRICAL_RESAMPLE` (it picks between the two per
   series), where every other dispatchable module (`fire_detections`, `sensors`,
   `vegetation_ndvi_forecast`, `water_gauges`) exports a flat `METHOD_NAME`. Fixing this means either
   changing `signal.py`'s public surface (a pre-existing, out-of-slice module whose two-method dispatch
   is presumably deliberate) or weakening the p2b-knn test's uniformity assumption — neither is
   mechanical, and the assertion is correct evidence of a real inconsistency. **Left as a finding** for
   whoever owns `signal.py`'s dispatch contract.

## Final gate state
- `ruff format --check`: clean (113 files).
- `ruff check`: clean.
- `mypy src scripts`: clean (58 files).
- `pytest -q`: 488 passed, 1 failed (`test_every_registry_stem_is_in_the_explicit_dispatch_map`, see above).

## Design requests left untouched (from the evidence files)
- Fire-risk §"Requests": `pipeline/AGENTS.md` merge, the `fire-risk` lane contract (values not
  supplied), the fire-risk availability bootstrap, `interface/cli.py`'s `predict-daily` wiring, a cell
  dimension for the scored universe, cell-resolved drought, and the `forecast_lane_bootstrap.py`
  row-select-strategy reconciliation (explicitly called "not mechanical" by its own author).
- KNN §"Requests": R1 (`availability_provenance_summary` promotion), R2 (`lane_identity_for` taking
  `nature`), R3/R4 (sensors/water-gauges lane + schema registration), R5 (a cumulative-index helper), R6
  (plan.md tripwire documentation).
- Weather-forecast §3: `docs/lanes/weather-forecast.md`, the RUNBOOK row, and the agri-data-service
  sibling registration (FR-12/FR-5a) — explicitly out of ML-service scope.

## p2b-fix-batch

Fix batch against `metadata.json` -> `reviews.phase2b` (2 blockers, 6 majors, 5 minors, the
coordinator decision, and the monitor's standing finding above). All edits under
`services/plantgeo-ml-service/`. No commit was made; `git add services/plantgeo-ml-service` ran
exactly twice, around the receipt refresh.

### B1 - the weather-forecast bootstrap marker

- `pipeline/weather_forecast_daily.py` - `_written_bootstrap` DELETED. The lane now writes the
  content-addressed receipt FIRST and emits the marker from it through the shared path:
  `_publish` (`weather_forecast_daily.py:378-415`) builds the terminal rows, then calls
  `ensure_lane_bootstrap(...)`, which is `forecast_lane_documents.bootstrap_lane` ->
  `bootstrap_marker_payload(lane_root, receipt)`.
- **Route chosen:** NOT `write_and_publish_forecast_rows`. That function is hard-bound to
  `kind=forecast`, to `observed_day` day-partitioning and to `FORECAST_TIER_DERIVATIONS`; the
  weather lane is `kind=observed`, `nature="release_series"`, partitioned on the provider issue day
  with future-ness in `valid_time`. It shares the BOOTSTRAP path, not the write path. The generic
  seam added for it is `bootstrap_lane`/`ensure_lane_bootstrap`, which take an
  `AvailabilityIdentity` instead of deriving a daily-series one from a layer slug.
- Test: `tests/test_weather_forecast_daily.py::test_the_bootstrap_marker_is_the_shape_the_siblings_reader_admits`
  asserts the marker's field set is exactly `{bootstrap_receipt_key, bootstrap_receipt_sha256,
  lane_root, schema_version}` (the sibling's `_BOOTSTRAP_MARKER_FIELDS`,
  `pipeline/parquet/availability_primitives.py:72`), that the bytes equal
  `bootstrap_marker_payload(...)` for the receipt this repo's own `read_lane_bootstrap_receipt`
  admits, and that the named receipt EXISTS and digests to what the marker claims.

### B2 - the fire-risk gate when there is no artifact

- `pipeline/fire_risk_gate.py:1` (new) - the FR-5 gate extracted whole. `run_fire_risk_daily`
  (`fire_risk_daily.py:181-190`) now raises `FireRiskPublicationGateError` on
  `artifact is None and not scratch_run`, before anything is written.
- **Empty source-receipt sha is now structurally impossible:** the availability source receipt is
  no longer `artifact_path(sha)` with an empty `sha`. Every fire-risk availability row cites the
  run's own immutable run document, written through `write_run_receipt`
  (`fire_risk_daily.py::_written_run_receipt`), the same object the other lanes cite. The artifact
  is named INSIDE it and on every data row.
- `forecast_run_id` (`fire_risk_daily.py:242-258`) digests four facts: the artifact sha or the
  DECLARED `NO_ARTIFACT_SENTINEL = "no-artifact"`, `issued_on` (the frontier), `run_date`, and the
  seed.
- Tests: `test_a_real_prefix_with_no_artifact_at_all_is_refused`,
  `test_two_artifactless_runs_of_different_days_never_collide`,
  `test_no_availability_row_cites_a_source_receipt_with_an_empty_digest`.

### M2 - the full day x rung ladder

- `pipeline/forecast_lane_bootstrap.py` - `write_forecast_day` no longer raises on an empty day; it
  returns a `ForecastDayReceipt` carrying `absence_reason` (default `UNFORECAST_DAY_REASON =
  "no_forecast_rows_produced"`). `terminal_rows_for_identity` projects BOTH terminal states over the
  whole rung ladder. `expected_forecast_days(issued_on, source_ceiling)` and
  `forecast_issue_frontier(layer, issued_on)` are the day ladder's two ends.
- `write_and_publish_forecast_rows` gained `issued_on` and `absence_reasons` and walks
  `[issued_on+1, source_ceiling]` union the days actually produced; an all-empty run now publishes
  rather than returning early, so "refused" and "never ran" are different in the index.
- `analog_ensemble_daily.py` - `_forecast_one_series` returns a `_SeriesOutcome` carrying
  `unanalogged_days`; `analog_count == 0` steps become governed absences with reason
  `NO_ANALOGS_REASON = "no_analogs"`.
- `monte_carlo_daily.py::_publish_lane_rows` - the `if not rows: return` early exit is gone; it
  passes `issued_on=inputs.cutoff` (the lane's settled frontier, which is what horizons counted from).
- `fire_risk_daily.py::_write_every_day` - walks the same ladder and emits absences per horizon day.
- `weather_forecast_daily.py` - a provider run with no readings publishes the issue day as a
  four-rung governed absence (`NO_READINGS_REASON`) instead of raising inside `_base_rows`.
- Tests: `test_forecast_lane_bootstrap.py::test_an_empty_day_returns_a_governed_absence_rather_than_an_empty_partition`,
  `::test_the_day_ladder_runs_from_the_issue_frontier_to_the_ceiling`,
  `test_fire_risk_daily.py::test_a_horizon_day_with_no_rows_is_published_as_a_governed_absence`,
  `test_analog_ensemble_daily.py::test_insufficient_history_refuses_with_a_reason_and_publishes_governed_absences`,
  `test_weather_forecast_daily.py::test_a_run_with_no_readings_publishes_the_day_as_a_governed_absence`.

### M3 - the backtest receipt is fetched and verified

- `pipeline/fire_risk_gate.py::verified_cleared_strata` loads `artifact.backtest.key` from the
  store, digests the bytes against `artifact.backtest.sha256`, decodes the document and reads
  `cleared_strata` FROM THE RECEIPT. A missing object, a digest mismatch, an undecodable body, or a
  receipt whose verdicts disagree with the artifact's claim all refuse with
  `FireRiskPublicationGateError`.
- Tests (all against the in-memory store): `test_the_cleared_strata_are_read_from_the_receipt_not_from_the_artifact`,
  `test_a_backtest_receipt_the_store_does_not_hold_refuses_the_run`,
  `test_a_backtest_receipt_whose_bytes_disagree_with_the_binding_refuses_the_run`,
  `test_an_artifact_that_disagrees_with_its_own_receipt_refuses_the_run`.
- The three `tests/fixtures/fire_risk/artifact-*.json` files are UNCHANGED on disk; the suite
  re-binds `backtest.key`/`sha256` in `tests/test_fire_risk_daily.py::artifact` to a receipt it
  actually writes, because a hand-written digest can never be verified.

### M4 - issued_on is the binding frontier

- `pipeline/fire_risk_features.py::binding_frontier(run_date)` = `min(settled_through(lane,
  run_date))` over `FIRE_RISK_INPUT_LANES` (fire-detections, vegetation, signal, drought,
  burn-severity). For 2026-09-19 that is **2026-09-10** (signal's nine-day lag binds).
- `build_fire_risk_features(reader, *, run_date, issued_on=None, ...)`: `run_date` bounds every lane
  read at that lane's own `settled_through`; `issued_on` labels the horizons and defaults to the
  frontier. `FireRiskFeatureFrame` carries both and `to_wire()` records both. Every internal lane
  reader's keyword was renamed `issued_on` -> `run_date`, so the two dates can no longer be confused.
- `run_fire_risk_daily(..., run_date=...)`; `FireRiskDailyReceipt` carries `issued_on` (frontier)
  and `run_date` separately; the prediction receipt is filed under `run_date`.
- Test: `test_fire_risk_features.py::test_horizon_one_never_precedes_the_binding_frontier` asserts
  the frontier is the minimum over the five lanes, that the `issued_on` COLUMN is the frontier, and
  that `min(valid_day) == frontier + 1 day > frontier`.

### M5 - one scratch-prefix check

- `pipeline/object_store.py` - `SCRATCH_PREFIX_ROOT`, `ScratchPrefixError` and
  `scratch_rooted_store(store, dry_run_prefix)` are the single implementation. Placed in
  `pipeline/object_store.py` rather than `foundation`: it constructs an `ObjectStore`, so it is not
  lattice-free.
- `fire_risk_gate.py::target_store` and `weather_forecast_daily.py::_target_store` both resolve
  through it and translate `ScratchPrefixError` into their own lane error.
- Test: `test_weather_forecast_daily.py::test_a_dry_run_prefix_outside_the_scratch_root_is_refused`,
  parametrized over a real lane prefix, a non-`ml/scratch/` root and a missing trailing slash, and
  asserting nothing was written.

### M6 - fire-risk gets a bootstrap through the shared path

- `fire_risk_daily.py::_publish` calls `ensure_lane_bootstrap(...)` with the config's identity and
  the run receipt, then `dataclasses.replace(availability, bootstrap_receipt=<the lane's own>)`.
  The marker ON the lane wins over whatever the caller declared: two bootstraps are two histories.
- Test: `test_fire_risk_daily.py::test_the_lane_root_is_bootstrapped_through_the_shared_path`.

### M7 + the coordinator decision

- `foundation/lattice.py:1` (new, stdlib-only L0) - INTEGER micro-degree lattice.
  `MICRO_DEGREES_PER_DEGREE = 1_000_000`; snap-to-origin (`LONGITUDE_ORIGIN_MICRO = -180e6`,
  `LATITUDE_ORIGIN_MICRO = -90e6`), floor to pitch by integer floor-division;
  `TIER_PITCH_MICRO = {9: 10_000, 5: 200_000, 0: 5_000_000}` with z13 deliberately absent as the
  base rung, exactly the pitches previously declared in three places. The module docstring states
  that ORIGINS are stored, never centroids.
- Importers: `pipeline/forecast_lane_rungs.py::floor_to_tier` (Polars expression, used by
  `derive_coarse_rung` and therefore by both `forecast_lane_bootstrap` and `fire_risk_daily`'s
  rungs) and `pipeline/weather_forecast_rows.py` (scalar `floor_coordinate`). The three private
  copies (`fire_risk_daily._floored_coordinate` + `TIER_RESOLUTION_MICRO`,
  `forecast_lane_bootstrap.floor_to_resolution` + `FLOOR_SNAP_TOLERANCE`,
  `weather_forecast_daily.floor_to_resolution` + its own tolerance) are gone.
- Parity test `tests/test_lattice.py` (94 cases): the integer form and the sibling's
  float + `FLOOR_SNAP_TOLERANCE` form (`agri-data-service .../warehouse/parquet/tiers.py:401`,
  restated in the test because `tiers.py` pulls in DuckDB) agree at 6 decimals on 15 edge longitudes
  x 3 rungs and 14 edge latitudes x 3 rungs, including negative longitudes, exact multiples of every
  pitch, the antimeridian, the equator and the 46-degree envelope edges the "Polars division is
  frame-length dependent" memory note names. Plus: origins divide evenly by every pitch (which is
  why snap-to-origin and the sibling's zero-anchored form agree at all), and the base rung refuses.
- `RowSelect(order_by, descending)` added to the shared coarse-rung vocabulary in
  `pipeline/forecast_lane_rungs.py`, beside `ColumnAggregation`. `GridAggregation` now declares
  EITHER `aggregations` OR `row_select` and refuses both/neither. `fire-risk` is registered
  declaratively in `FORECAST_TIER_DERIVATIONS` with
  `RowSelect(order_by=("risk_score", "probability", "refused_reason"), descending=(True, True, False))`.
  Quantile fields keep the per-`(quantile, horizon_days, forecast_run_id)` mean: the six provenance
  columns join the grain in BOTH strategies. Provider variables keep mean with wind recomposed from
  averaged u/v.
- Tests: `test_fire_risk_daily.py::test_a_coarse_fire_risk_row_is_a_whole_row_the_model_produced`
  (every field of a coarse row comes from ONE base row - stratum and risk_score from the same source
  row - and it is the worst of them);
  `test_forecast_lane_bootstrap.py::test_a_quantile_coarse_row_is_the_mean_of_its_cells_never_the_worst_of_them`
  (10.0 and 16.0 merge to 13.0, not 16.0);
  `::test_the_fire_risk_lane_declares_a_row_select_over_the_shared_vocabulary`.

### Minors

- **Wind catalogue inversion declared.** `warehouse/weather_forecast.py:59` - the comment claimed
  wind is published as the component pair with speed/direction derived; the catalogue says the
  opposite and the catalogue is right. The doc-comment now states that Open-Meteo answers
  `wind_speed_10m`/`wind_direction_10m` and this service DERIVES `wind_u_10m`/`wind_v_10m`, names
  the probe `.omc/research/forecast-s3-probe-20260919/`, and calls out the two consequences
  (`derived_vector_magnitude` on a base-rung scalar; all four absent together). No value changed.
- **`paired_entry_indexes` docstring corrected.** `pipeline/sources/open_meteo_parsing.py` - the
  docstring claimed order is not trusted and pairing is re-derived from snapped coordinates; the
  function returns the identity permutation. It now says what actually guards the pairing:
  `location_id` is the identity check, and the snap ceiling and the beneficial-swap search are
  corroboration.
- **Aggregated `support` is a merge.** `pipeline/weather_forecast_rows.py::_merged_support` - the
  unanimous value, else `MERGED_SUPPORT = "derived_field"` (the most conservative of
  `SUPPORT_KINDS`). Was `rows[0]["support"]`.
- **AnEn rows no longer inherit `newest_observed_at`.** `analog_ensemble_daily.py::_rows_for_step`
  writes `identity.issued_at` (the run's instant). The signal schema declares the column
  non-nullable, so the run instant is the honest value rather than a null (layer-lanes section 3).
- **Modules over the 600-line ceiling split, with re-exports so no import moved:**
  - `monte_carlo_daily.py` 691 -> **399** (+ `monte_carlo_adapters.py` 367: the three per-lane
    reshaping adapters, `MonteCarloOptions`, `LaneSimulationInputs`, `MonteCarloDispatchError`)
  - `sources/open_meteo.py` 664 -> **328** (+ `sources/open_meteo_parsing.py` 407: pairing, hours,
    samples, run identity, URL redaction - the pure half)
  - `fire_risk_features.py` 629 -> **475** (+ `fire_risk_geometry.py` 105 WKB envelope scan,
    + `fire_risk_plane.py` 154 frame assembly and the feature vocabulary)
  - `weather_forecast_daily.py` 605 -> **499** (+ `weather_forecast_rows.py` 225 row shaping)
  - `forecast_lane_bootstrap.py` grew past the ceiling under M2/M7 and was split the same way:
    **459** (+ `forecast_lane_rungs.py` 262 vocabulary and derivation, + `forecast_lane_documents.py`
    198 the two bootstrap documents)
  - `fire_risk_daily.py` grew past the ceiling under B2/M3/M4 and was split: **555**
    (+ `fire_risk_gate.py` 111)
  - Still over and NOT in scope here (all pre-existing): `recommendation_models.py` 1363,
    `strategy_selection.py` 912, `covariate_wind_model.py` 703, `monte_carlo/sensors.py` 670.
    `monte_carlo/signal.py` (666 -> 681) and `pipeline/object_store.py` (627 -> 654) were already
    over the ceiling before this batch and grew by the documentation block and the shared scratch
    helper respectively.

### Monitor's standing finding - method/monte_carlo/signal.py

`method/monte_carlo/signal.py:51` now exports `METHOD_NAME` as the dispatch's contract, aliased to
`METHOD_NAME_ADDITIVE_ANOMALY` - the estimator every signal but precipitation uses. **Neither
method-name STRING changed**, and no output moved: a row's `method_name` is still whichever of the
two its own `SignalForecastRun` recorded, selected per series by `SignalSeriesSpec.bootstrap_kind`.
Both names remain public because both are real methods, and a new `METHOD_NAMES` tuple closes the
set a reader may check against. The doc-comment states why this module has two where every sibling
has one. `tests/test_monte_carlo_daily.py::test_every_registry_stem_is_in_the_explicit_dispatch_map`
is green honestly: `load_forecast_module("signal").METHOD_NAME` resolves, and the test was not
weakened.

### Fixtures

No parity fixture was regenerated; `scripts/regenerate_parity_fixtures.py` was not run. No case was
added to `tests/parity_parquet_cases.py` or `tests/parity_cases.py`. The three fire-risk artifact
fixtures are byte-unchanged on disk (the suite re-binds the backtest reference at load time, per M3
above), and `tests/fixtures/open_meteo/multi-location-run-20260918.json` is unchanged.

### Gates - one sweep after all fixes, in services/plantgeo-ml-service

| gate | result |
|---|---|
| `uv sync --locked --all-extras` | Resolved 63 packages, audited 61 - lock unchanged |
| `uv run --no-sync ruff format src tests scripts` | 123 files, all formatted |
| `uv run --no-sync ruff format --check src tests scripts` | **123 files already formatted** |
| `uv run --no-sync ruff check src tests scripts` | **All checks passed** (0 errors) |
| `uv run --no-sync mypy src scripts` | **Success: no issues found in 67 source files** |
| `uv run --no-sync pytest -q` | **604 passed**, 2 warnings, 19s (was 488 passed / 1 failed) |

The two warnings are pre-existing `RuntimeWarning: invalid value encountered in divide` from
`method/ml/seasonal_evaluation.py:468,475`, untouched by this batch.

A second pass was needed only for mechanical fallout from these edits (import re-sorting and four
`TYPE_CHECKING` moves after the module splits, one `Final[Mapping[ZoomTier, int]]` annotation, and
five `PLR2004` literals hoisted in the new lattice test). No test was weakened to make a gate pass.

### Receipt

- `git add services/plantgeo-ml-service`
- `uv run --no-sync python scripts/check.py --write-receipt` -> format PASS 0.05s, lint PASS 0.05s,
  mypy PASS 1.26s, pytest PASS 20.62s
- `git add services/plantgeo-ml-service` (again, to stage the refreshed receipt)
- **Receipt sha256:** `5f962b48c2c5e3bf0931b612e4ec974abf621973bf42a09bedcafc1abc1751df` over **142 files**
- `python scripts/verify_quality_receipt.py` -> `quality receipt verified: sha256:5f962b48...`,
  **exit 0**

Nothing was committed.
