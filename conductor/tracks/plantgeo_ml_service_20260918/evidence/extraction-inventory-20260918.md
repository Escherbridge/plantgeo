---
type: evidence
track: plantgeo_ml_service_20260918
measured_at_commit: 8451ebcf
---

# Extraction inventory — what moves, what dies, what stays

Paths are relative to `services/agri-data-service/`. Re-verify with a grep before editing; HEAD moves.

## A. Moves to `services/plantgeo-ml-service/src/plantgeo_ml_service/` (pure, no Postgres)

| from | to | notes |
|---|---|---|
| `src/agri_data_service/method/ml/*.py` (9 + `__init__`) | `method/ml/` | rewrite `agri_data_service.foundation` imports to `plantgeo_ml_service.foundation` |
| `src/agri_data_service/method/monte_carlo/*.py` (5 + `__init__`) | `method/monte_carlo/` | same; `vegetation_ndvi_forecast.py` here is the L1 copy; the execution copy STAYS |
| `src/agri_data_service/method/AGENTS.md` | `method/AGENTS.md` | keep the expert-label/covariates/recommendation rationale verbatim |
| `src/agri_data_service/foundation/canonical.py` (only `canonical_json`, `sha256_digest`, `validate_finite`) | `foundation/canonical.py` | copy the three functions; parity test against the source |
| `src/agri_data_service/execution/strategy_selection.py` (912) | `pipeline/strategy_selection.py` | numpy only; local-file bundle in, artifact out |
| `src/agri_data_service/execution/strategy_label_mapping.py` (300) | `pipeline/strategy_label_mapping.py` | needs `execution/contracts.py::canonical_json_bytes, reject_credential_url` — copy those two helpers into `foundation/contracts.py` |
| `examples/strategy-label-source-mapping.incomplete.json` | `examples/` | the worked example for `strategy-label-map-preflight`; moves with the verb (added to this list after the cut review found it untraced) |
| `interface/cli/ml.py` + the two verbs in `interface/cli/commands.py` (`strategy-label-map-preflight`, `strategy-train`, `_write_atomic`) | `interface/cli.py` | become `plantgeo-ml strategy-train` / `strategy-label-map-preflight` |
| tests: `test_analog_ensemble.py`, `test_conformal_calibration.py`, `test_covariate_wind_model.py`, `test_expert_label_plane.py`, `test_recommendation_models.py`, `test_seasonal_candidates.py`, `test_seasonal_evaluation.py`, `test_seasonal_lineage_graph.py`, `test_strategy_selection.py`, `test_strategy_label_mapping.py` | `tests/` | `test_covariate_wind_model.py` targets `execution/covariate_wind_model.py` (814 lines), which is NOT pure: it imports sqlalchemy and loads `target_signal_series.sql` at module scope. Extract the numeric core into `method/ml/covariate_wind_model.py`; the query surface dies with §B; adapt the test to the extracted core |
| forecaster sections of `tests/parquet/test_signal_serving.py` (imports :24-38, section from :290) and `tests/parquet/test_vegetation_serving.py` (imports :27-37, section from :475) | `tests/test_signal_forecast.py`, `tests/test_vegetation_forecast.py` | observed-side sections stay untouched; the two files are not modified by the concurrent session (git status clean 2026-09-18) |

## B. Deleted from agri-data-service (Postgres-coupled, dead, re-expressed against Parquet in phase 2)

Execution: `analog_ensemble_cli.py`, `analog_ensemble_model.py`, `analog_ensemble_persist.py`,
`conformal_recalibration.py`, `covariate_wind_lane.py`, `covariate_wind_persist.py`,
`forecast_receipt_writer.py`, `recommendation_commands.py`, `recommendation_lane.py`,
`seasonal_benchmark.py`, `seasonal_command.py`, `seasonal_evaluation_export.py`,
`seasonal_evidence_report.py`, `seasonal_lineage_persist.py`, `seasonal_row_types.py`.

Routes: `routes/recommendations.py` (unmounted).

SQL, **derived mechanically** (every `.sql` whose `load_query_sql` call sites all lie in a §A-moved or
§B-deleted module; re-run the derivation after the deletion because
`tests/test_sql_tree_conventions.py::test_loaded_exactly_once` fails on any orphan) — 40 files:
`sql/routes/recommendation_pinned_artifact.sql`, `sql/routes/recommendation_subject_citations.sql`, and
under `sql/execution/`: `advance_expert_label_review`, `covariate_daily_features`,
`covariate_vector_manifest`, `insert_expert_label`, `insert_expert_label_release`,
`insert_expert_label_source`, `insert_expert_label_training_instance`, `insert_forecast_backtest_metric`,
`insert_forecast_feature_snapshot`, `insert_forecast_model`, `insert_forecast_run`,
`insert_forecast_training_run`, `insert_job_output`, `insert_model_artifact`,
`insert_recommendation_training_receipt`, `insert_training_job_definition`, `insert_training_job_run`,
`seasonal_insert_candidate_evaluation`, `seasonal_insert_candidate_evaluation_origin`,
`seasonal_insert_derived_signal_value`, `seasonal_insert_lineage_edge`, `seasonal_insert_signal_definition`,
`seasonal_iteration_inventory`, `seasonal_iteration_origin_histogram`, `seasonal_observation_plane_totals`,
`seasonal_release_lineage`, `seasonal_series_daily_export`, `seasonal_series_profile`,
`seasonal_series_registry`, `seasonal_source_landing_census`, `select_covariate_vectors`,
`select_daily_signal_series`, `select_forecast_iteration_residuals`, `select_matched_training_instances`,
`select_trainable_expert_labels`, `select_validated_release_set`, `target_signal_series`,
`validate_training_run`. **Keep** `insert_forecast_series`, `insert_forecast_iteration`,
`insert_forecast_iteration_value`, `reconcile_forecast_iteration_actuals` (loaded by
`execution/vegetation_ndvi_plane.py`, which stays). `db/_loader_smoke.sql` is orphaned today already and is
not this track's.

Tests: `test_covariates_v2_schema.py` (live Postgres: psycopg2 + `AGRI_TEST_DATABASE_URL`, fails rather than skips), `test_analog_ensemble_model.py`, `test_analog_ensemble_persist.py`,
`test_analog_ensemble_persist_postgresql.py`, `test_conformal_recalibration.py`,
`test_covariate_wind_lane.py`, `test_covariate_wind_persist.py`, `test_expert_label_plane_postgresql.py`,
`test_recommendations_route.py`, `test_seasonal_evaluation_export.py`,
`test_seasonal_lineage_persist_postgresql.py`, `test_strategy_selection_gates_postgresql.py`,
`test_covariate_feature_layer_postgresql.py`, `test_signal_lineage_postgresql.py`.
(Check each `*_postgresql` file's body before deleting: keep any that only exercise tables still in
`db/agri_baseline.sql`.)

CLI: `interface/cli/ml.py`; the two verbs and `_write_atomic` in `interface/cli/commands.py`; the
`ml` group registration in the CLI root.

## C. Edited in agri-data-service

- `src/agri_data_service/__init__.py` — drop the `SeasonalHistory`/`simulate_horizon_quantiles` re-exports (no external importer found).
- `tests/test_layer_import_contract.py:480-482` — `CLI_ADAPTER_VIOLATIONS` pins `commands.py:146`, which moves when the verbs above it are deleted; regenerate from the sweep's assertion message.
- `tests/test_layer_import_contract.py` — remove `SUBPACKAGE_FORBIDDEN_IMPORTS` and `"method/monte_carlo"` from `SIBLING_MODULE_DIRECTORIES`; keep the `method` layer rules for `method/AGENTS.md`-less remainder (the directory may become empty; if so delete `method/` and its layer entry).
- `src/agri_data_service/pipeline/parquet/lane_registry.py:149-153` — comment: the stem names a `plantgeo_ml_service.method.monte_carlo` module; behaviour unchanged.
- `src/agri_data_service/execution/AGENTS.md`, `src/agri_data_service/AGENTS.md`, `docs/lanes/*.md` §7, `conductor/code_styleguides/layer-lanes.md` §5 — one-paragraph pointer to the ML service; no rewrites.
- `pyproject.toml` — `scikit-learn` leaves the dependency list if nothing else imports it (grep first: `analysis/` is outside `src/`).
- `QUALITY_RECEIPT.json` — refreshed by the single end sweep (`python scripts/check.py --write-receipt`), never by hand.

## D. Stays, do not touch (other session's partition or live path)

`execution/vegetation_ndvi_forecast.py`, `execution/vegetation_ndvi_plane.py`,
`execution/vegetation_partition_promotion.py`, `execution/lane_specs.py`, `execution/ensemble_forecast.py`,
`execution/public_evaluation_*`, `models/forecasting.py`, `models/strategy_selection.py`,
`routes/strategies.py` (community reference lookup, not ML), `agent/tools.py`, `analysis/**`,
everything under `pipeline/`, `planes/`, `parquet_ops/`, `warehouse/`, and all of `src/**` (web). The observed-side sections of `tests/parquet/test_signal_serving.py` and `test_vegetation_serving.py` stay; only their forecaster sections move (§A).
