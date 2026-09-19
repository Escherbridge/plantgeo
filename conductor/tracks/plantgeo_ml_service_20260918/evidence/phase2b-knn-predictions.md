---
type: track-evidence
slice: p2b-knn
phase: 2B
date: 2026-09-19
status: authored-not-verified
---

# Phase 2B (`p2b-knn`) — Analog Ensemble and Monte Carlo forecast lanes

Author slice. Per the track rule, this slice ran **no** `pytest`, `ruff` or `mypy`; it ran smoke
imports only (see "What was actually executed"). Everything below that claims a behaviour is a
prediction for the monitor sweep to confirm or refute.

## Files created

All under `services/plantgeo-ml-service/`.

| file | role |
|---|---|
| `src/plantgeo_ml_service/pipeline/covariate_vectors.py` | standardized AnEn covariate vectors from `layer=signal/kind=observed` |
| `src/plantgeo_ml_service/pipeline/analog_ensemble_daily.py` | daily AnEn lane, horizons 1..30, p10/p50/p90 |
| `src/plantgeo_ml_service/pipeline/monte_carlo_daily.py` | explicit `importlib` dispatch to the five `method/monte_carlo` stems |
| `src/plantgeo_ml_service/pipeline/forecast_lane_bootstrap.py` | rung ladder, bootstrap receipt + marker, terminal rows, publication |
| `src/plantgeo_ml_service/pipeline/AGENTS-forecast-lanes.md` | rationale for the four modules |
| `tests/test_covariate_vectors.py` | pinned order, partial-stays-partial, frontier refusal |
| `tests/test_analog_ensemble_daily.py` | end-to-end AnEn lane over the in-memory store |
| `tests/test_monte_carlo_daily.py` | dispatch map, fire-detections lane end to end |
| `tests/test_forecast_lane_bootstrap.py` | ladder arithmetic, bootstrap byte-parity, shared harness |

No file outside the declared ownership list was created, edited or deleted.

## Design decisions worth a reviewer's attention

1. **`forecast_source_ceiling(lane, issued_on, horizon) = settled_through(lane, issued_on) + horizon`.**
   `AvailabilityRow` refuses `day > source_ceiling`, and every forecast day is beyond the provider
   frontier by construction, so a bare frontier could never be the ceiling for a forecast lane. The
   horizon is added to the FRONTIER, never to `date.today()` — this is the 2026-09-19 staleness
   tripwire honoured in the only place it can bite.
2. **A forecast lane's availability generation is issue-scoped**, holding this run's horizon days
   rather than accumulating every day the lane ever published. Yesterday's horizon-30 row and
   today's horizon-29 row for one day are two answers to one question; the newer run supersedes.
   Flagged explicitly because it differs from the observed lanes' cumulative index.
3. **`observation_count = 0` and `release_count = 0` on forecast rows.** A forecast rests on zero
   observations. Filling these with the analog or draw count would read as measurement support.
   `newest_observed_at` / `data_available_at` ARE propagated, because they are facts about the
   history the run was issued from.
4. **Vegetation's `observation_checksum` carries the forecast row's own fingerprint.** That column
   is in `base_non_null_columns`, so a base-rung forecast row cannot leave it null, and inheriting
   the observation's checksum would be exactly the borrowed lineage section 3 forbids.
5. **Zero-analog AnEn steps are dropped, not published.** `generate_anen_forecast` falls back to a
   flat persistence value; publishing that as p10/p50/p90 would state a spread that no ensemble
   produced. A series with no surviving step becomes a `no_analog_successors` refusal.
6. **`sensors` and `water_gauges` are in the dispatch map but not in `STEM_LAYERS`.** Both modules
   import and are executable; neither has a `LaneContract` or a pinned observed schema in
   `warehouse/`, so dispatching them refuses by name rather than inventing a stream to write.

## Requests for changes outside this slice's ownership

| # | file (not touched) | request | why |
|---|---|---|---|
| R1 | `warehouse/availability.py` | add `availability_provenance_summary(rows)` | the sibling's bootstrap receipt requires it verbatim; it is currently copied into `forecast_lane_bootstrap.py` and belongs beside the other document code |
| R2 | `warehouse/availability.py` | let `lane_identity_for` take the lane's `nature` instead of hardcoding `"daily_series"` | correct for all three lanes written today, wrong the moment a `release_series` lane (drought, burn-severity) gains a forecaster |
| R3 | `warehouse/lanes.py` | register `sensors` and `water-gauges` contracts, or record that both stay out of the Parquet warehouse | two shipped forecasters currently have nowhere to write; the dispatcher refuses them by name today |
| R4 | `warehouse/streams.py` | add `sensors` / `water-gauges` observed schemas alongside R3 | same gap, schema half |
| R5 | `pipeline/availability_publisher.py` | a read-modify-write helper if a forecast lane ever needs a CUMULATIVE index | not needed for decision 2 above; named so the choice is visible rather than assumed |
| R6 | `conductor/tracks/.../plan.md` Tripwires | the two tripwires added 2026-09-19 (staleness from the provider frontier; classify every day by the full rung ladder) are NOT in `plan.md` as of this slice's read | both were implemented from the brief's wording (decision 1 above, and `write_forecast_day` writing all four `ZOOM_TIERS` with a marker each); the plan file should carry them |

## Predicted sweep failures

Ordered by how likely they are to be real.

1. **`mypy --strict` on `monte_carlo_daily.py`: `Any` from `importlib.import_module`.** Every
   `module.ObservedCellDay(...)` / `module.simulate_*` call returns `Any`. This is inherent to a
   dynamic dispatch map; the fix is either a `Protocol` per forecaster or a narrow, reasoned
   `# type: ignore` at each adapter boundary. Not pre-emptively silenced, because the reviewer
   should choose.
2. **`ruff` line length / complexity in the three simulation adapters.** Each builds a wide row
   dict; `_simulate_signal` and `_simulate_vegetation` may trip `PLR0912`/`C901`.
3. **`pl.lit(None).cast(dtype)` inside `derive_coarse_rung`** may warn or need
   `pl.lit(None, dtype=...)` on some Polars versions. Verified against polars 1.44.2 only by
   reading the API, not by running the derivation.
4. **`test_identical_inputs_write_byte_identical_partitions`** compares two whole object dicts. If
   anything in the write path reaches a clock not pinned by `created_at=MOMENT`, this is the test
   that fails, and it should be read as a real determinism finding rather than a flaky fixture.
5. **The fire-detections end-to-end test writes 60 days x 4 rungs** plus a 200-draw ensemble. It is
   the slowest test in the file; if the suite has a per-test time budget it may need trimming.
6. **`test_a_lane_with_no_forecaster_is_refused_rather_than_guessed`** asserts on `drought`, which
   has `forecast_module=None` today. A later slice arming a drought forecaster flips this test.
7. Sibling parity: `bootstrap_receipt_payload` was written from a READ of
   `agri_data_service/pipeline/parquet/availability_requests.py::_bootstrap_receipt_payload`, not
   from an executed comparison. A parity case that round-trips one receipt through the sibling's
   own parser is the verification this slice could not perform.

## What was actually executed

Smoke imports only, with the service's own `.venv`:

- `import plantgeo_ml_service.pipeline.{covariate_vectors, analog_ensemble_daily, monte_carlo_daily, forecast_lane_bootstrap}` — all import.
- `monte_carlo_daily.dispatchable_lanes()` returns `('fire-detections', 'signal', 'vegetation')`;
  `load_forecast_module('water_gauges').METHOD_NAME` resolves.
- All four new test modules import cleanly (module-level code only; no test was run).
- One three-line Polars API probe (`pivot` keyword signature, `partition_by(as_dict=True)` key
  shape) against polars 1.44.2. Disclosed because it is more than an import.

No test, lint or type check was run, and no claim above rests on one.
