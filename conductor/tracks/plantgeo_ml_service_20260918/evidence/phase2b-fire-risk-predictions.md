---
type: track-evidence
slug: phase2b-fire-risk-predictions
track: plantgeo_ml_service_20260918
slice: p2b-fire-risk
authored_on: 2026-09-19
---

# Slice `p2b-fire-risk`: files, requests, and predicted sweep failures

Authored without running pytest, ruff or mypy (owner rule 2026-08-25: authors predict what will fail,
a separate monitor judges). Smoke imports of all four modules and all four test modules pass, and the
polars API surface used here was exercised directly (`1.44.2`): integer floor division on `Int64`,
`dt.ordinal_day`, `dt.year`, `sin`/`cos`/`tan`/`arccos`/`clip`, `join(how="cross")`,
`unique(..., keep="first", maintain_order=True)`, `group_by().agg(pl.len())`, `pl.min_horizontal`,
`Expr.filter(...).mean()` inside an aggregation, `to_numpy()` nulls becoming NaN.

## Files created

| file | what it is |
|---|---|
| `src/plantgeo_ml_service/pipeline/fire_risk_features.py` | leakage-gated feature builder, lattice arithmetic, WKB envelope scan, per-row checksum |
| `src/plantgeo_ml_service/method/ml/fire_risk_model.py` | numpy-only ridge logistic, PAVA calibration, canonical-JSON artifact, typed refusals |
| `src/plantgeo_ml_service/pipeline/fire_risk_backtest.py` | walk-forward per stratum vs VPD-only and climatology, declared minima, backtest receipt |
| `src/plantgeo_ml_service/pipeline/fire_risk_daily.py` | daily run, FR-5 gate, four rungs per valid day, availability publication, prediction receipt |
| `src/plantgeo_ml_service/pipeline/AGENTS-fire-risk.md` | rationale, for the coordinator to merge into `pipeline/AGENTS.md` |
| `tests/test_fire_risk_features.py` | leakage, stratum, lattice, seasonality, determinism, the real reader over an absent warehouse |
| `tests/test_fire_risk_model.py` | five refusals, artifact round trip and digest tamper, fit direction, calibration monotonicity |
| `tests/test_fire_risk_backtest.py` | a stratum that clears, a stratum only climatology predicts, receipt reproducibility |
| `tests/test_fire_risk_daily.py` | gate refusals, rung ladder, provenance columns, byte-identical partitions, pointer advance |
| `tests/fixtures/fire_risk/artifact-{cleared,withheld,ungated}.json` | three stored artifacts, digests computed by the shipped encoder |

Nothing outside the slice's ownership was edited. No git commands were run.

## Requests for changes outside this slice's ownership

1. **`pipeline/AGENTS.md` merge (coordinator).** Fold `pipeline/AGENTS-fire-risk.md` in as a section
   and delete the standalone file. It is written to drop in without renumbering anything.
2. **`warehouse/lanes.py` (`p2d-fire-risk-registration`).** `fire-risk` has no `LaneContract` here and
   nothing in this slice calls `lane_contract("fire-risk")`. The registration slice owes the contract
   (`daily_series`, cadence 1, a cited history floor) on both sides, plus `docs/lanes/fire-risk.md`.
   Until it lands, a reader asking this service for the fire-risk lane's clock gets a refusal.
3. **Availability bootstrap for `layer=fire-risk/kind=forecast` (lanes slice).** `run_fire_risk_daily`
   publishes only when it is handed an `AvailabilityConfig` and a `PointerStore`. The lane's
   generation-zero `availability/bootstrap/_BOOTSTRAPPED.json` is still unwritten, exactly as
   `pipeline/AGENTS.md` records for the other forecast lanes, so acceptance 5.2 cannot be met for
   fire-risk until it exists.
4. **`interface/cli.py` (`p2c-api`).** `predict-daily` should call
   `run_fire_risk_daily(reader, store, artifact, issued_on=..., dry_run_prefix="ml/scratch/<date>/",
   completed_at=..., ml_prefix=settings.ml_prefix)` and let the returned `FireRiskDailyReceipt`
   be the turn's receipt. The default `ml_prefix="ml/"` in this module matches what
   `config.Settings.normalize_prefix` produces; the CLI should still pass the setting rather than
   rely on the default.
5. **A cell dimension, or an explicit decision to live without one.** The scored universe is the cells
   the `fire-detections` lane holds rows for in the trailing 28 days, which is biased towards cells
   that have recently burned. A region cell dimension (or a rung-selected NDVI cell list) would make
   the universe honest. Recorded rather than papered over.
6. **Cell-resolved drought.** `regional_drought_category` is ONE scalar for the whole region, because
   a USDM release is a CONUS-wide multipolygon and the bounding-box trick used for fire perimeters
   would assign every cell the same class. A cell-resolved version needs a DuckDB spatial clip on the
   read path (the `analysis/AGENTS.md` recipe) or a pre-reduced drought-by-cell lane.
7. **Overlap with the concurrent slice's `pipeline/forecast_lane_bootstrap.py` (coordinator call).**
   That file appeared in the tree during this slice and covers the same ground as this one's
   `_write_day`, `_availability_row` and `_publish`: `write_forecast_day` writes every rung with its
   completion marker, `terminal_rows_for_day` projects the ladder, `write_and_publish_forecast_rows`
   publishes, and `bootstrap_forecast_lane` writes the marker request 3 asks for. Delegating to it
   would remove roughly eighty lines here, and I did not do it because the file is another slice's
   partition and was still moving.
   **The reconciliation is not mechanical.** `derive_coarse_rung` dispatches on
   `FORECAST_TIER_DERIVATIONS`, which has no `fire-risk` entry, and its `ColumnAggregation`
   vocabulary is per COLUMN: the closest expressible rule (`max` on `risk_score`) would pair one
   row's score with another row's `stratum` and `refused_reason`, which is a coarse row no model ever
   produced. Fire-risk needs a whole-ROW select, so either that module gains a row-select strategy or
   fire-risk keeps its own `_coarsened`. My recommendation is the row-select strategy, because the
   same question returns for every lane whose rows carry a refusal reason.
8. **`tests/AGENTS.md` (coordinator).** Worth one line: `test_fire_risk_daily.py` imports the seeded
   lane fixtures from `test_fire_risk_features.py`, so one warehouse fixture serves both suites.

## Decisions a reviewer should challenge first

- **The stratum is a declared NDVI floor (0.6), not the analysis's greenness quartile.** A quartile is
  computed on the scoring day's own population, so it is a different definition every day and an
  artifact could not be reproduced against it. The cost is that the floor is a judgement call sited on
  the 2026 plane; it travels in `FEATURE_SET_VERSION` so a change is visible.
- **A coarse rung is a rung-select of the worst cell, not an average.** Averaging probabilities over a
  5 degree cell would be a modelling claim nobody fitted.
- **The partition day is the VALID day, not the issue day.** The issue day and horizon live in the
  provenance columns. A reader asking for a future day therefore reads one prefix.
- **`quantile` is written as the number 0.5.** The pinned `FIRE_RISK_SCHEMA` types it `float64`, so
  the brief's "p50" is a label a reader renders, not a stored string.
- **`MINIMUM_PR_AUC_LIFT = 0.02` over BOTH baselines, `MAXIMUM_BRIER_EXCESS = 0.0`.** Deliberately
  close to the measured in-sample margin over VPD alone (about 0.03). The gate is meant to be hard.

## Predicted sweep failures, in the order I expect them

1. **`ruff` `TC001`/`TC003` on annotation-only imports.** `fire_risk_daily.py` imports
   `FireRiskArtifact`, `AvailabilityConfig` and `ParquetWriteReceipt` at runtime although they appear
   only in annotations, and `fire_risk_backtest.py` imports `date`/`datetime` for annotations. I left
   them at runtime because `pipeline/observed_reader.py` uses the same shape for `PartitionKind` and
   passes today; if the monitor sees the finding, the fix is to move those three names into the
   `TYPE_CHECKING` block. Low risk, mechanical.
2. **`ruff` `PLR2004` (magic value comparison) in the four test modules.** Rung numbers (`13`), horizon
   counts and `0`/`1.0` comparisons appear in assertions. The existing suites have the same shape, so
   either the rule tolerates it or all of them fail together.
3. **`mypy --strict` on `pl.from_arrow` and `Series.min()/max()` returns.** `_training_span` in the
   backtest annotates the `min()`/`max()` of a Date column as `date` with a coded ignore; if the
   installed polars stubs type those as `Any`, the ignore becomes unused and `--strict` will say so.
4. **`test_fire_risk_backtest.py` fold thresholds.** `MINIMUM_FOLD_ROWS = 200` and
   `MINIMUM_FOLD_POSITIVES = 10` are enforced per (stratum, year); the synthetic plane gives 300 rows
   and 150 positives per fold, so the margin is wide, but if either constant is raised the two
   `fold_count == 2` assertions fail before anything else does.
5. **`test_fire_risk_daily.py::test_a_missing_artifact_refuses_every_row_and_writes_no_score`** asserts
   `refusal_counts == {artifact_missing: base_rows * len(HORIZONS)}`. That identity holds only because
   the fixture warehouse has exactly one fire cell; a second cell in the fixture changes the arithmetic
   rather than the behaviour.
6. **The absent-warehouse test is the slowest thing here.** It writes 1,203 governed-absence markers
   into the in-memory backend (burn-severity alone spans 1,096 days) and the in-memory `list_objects`
   is a full scan per call. Expect seconds, not milliseconds; if it is worse than that, the fixture
   should seed the burn lane with one data day and shorten `_LANE_SPANS`.
7. **An `AvailabilityConfig` with a missing artifact raises rather than publishing.** The availability
   row names the artifact as its source receipt, and `require_sha256` refuses the empty digest a
   missing artifact carries. That combination (publish a lane while refusing every row for lack of a
   model) is not a shape this slice supports, and I would rather it raise than publish a generation
   whose source receipt is a lie. If the monitor disagrees, the fix is a typed refusal in
   `_availability_row` naming the case.
8. **Windows-versus-Linux float rendering in the receipt digests.** The prediction receipt and the
   backtest receipt digest canonical JSON over floats. Nothing here rounds, so a platform that renders
   a float differently would change a digest; Python's `repr` is platform-stable for IEEE doubles, so I
   expect this to hold, and I name it because the QA image is Linux and this was authored on Windows.

## What was deliberately not done

- No model was trained against production data, and no run was executed against the production bucket.
  `train_fire_risk_model` exists because the backtest needs it.
- No `fire-risk` registration, no `docs/lanes/fire-risk.md`, no RUNBOOK edit, no change to
  `warehouse/streams.py`, `object_store.py`, `availability_publisher.py`, `config.py` or
  `pipeline/AGENTS.md`.
