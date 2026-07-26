# SQL-first forecasting framework

**Status:** implementation contract. The schema and SQL baseline are enabled only for validated local inputs; no forecast or ML result is implied by installing it.

## Design

PlantGeo normalizes every forecastable metric to one `forecast_series` identity: source variant, entity, metric, unit, spatial support, temporal support, and optional spatial cell. Entity changes are immutable `forecast_entity_state` versions, and observations reference both the source release and the applicable state version. A row marked `raw_native` retains native support; a resampled or aggregated row must name its method, source support, output support, and feature recipe. Pre-aggregates never become observations.

`forecast_feature_snapshot` pins a validated `release_set`, its manifest checksum, the feature recipe/code checksum, and the resulting feature checksum. `forecast_model` and `forecast_training_run` preserve local training lineage and validation evidence. ML forecast runs are rejected unless their training run and feature snapshot are validated. SQL linear-regression runs use the same snapshot boundary and require stored historical holdout metrics to pass a versioned `forecast_quality_policy`.

Forecast values are staged beneath a `forecast_receipt`. Finalization recomputes a deterministic SHA-256 digest, checks the declared count and valid-time extent, and requires a validated run. Finalized receipts and values are append-only. A separate publication groups finalized receipts and advances only after every item passes validation. Installation creates no runs, receipts, values, publications, or model predictions.

The serving contract has two layers:

- `v_forecast_series_serving` exposes published point/band series with issue and valid times, source/model/feature lineage, quality metrics, and explicit native/output support.
- `mv_forecast_ml_daily_serving` selectively pre-aggregates only published ML series opted into aggregation. It labels every row `preaggregated_forecast`, retains the originating support metadata and receipt checksum, and is refreshed only by an explicit reviewed call.

Agent queries can use the point contract or the daily materialization. Map/chart rendering uses the same versioned rows and spatial cell IDs; neither contract reads generic artifacts or the local hot-projection manifest.

## Historical hindcast signals

Revision `20260722_0006` persists retrospective SQL-linear evaluations outside
the operational issue-time tables. Each hindcast has a
`simulated_cutoff_time`, a separately recorded database availability time, an
explicit `retrospective_pinned_release` label, and immutable per-step point,
empirical interval, last-value baseline, actual-release, observation-checksum,
residual, and error evidence. Finalization reruns the cutoff-bound regression
and prior-window residual calibration before accepting the receipt. It does not
pretend that the currently pinned source release was available at the simulated
date.

`v_forecast_hindcast_outcome` exposes the wide evaluation record.
`forecast_hindcast_signal_timeseries(series_id, model_id, signal_kind,
horizon_step, as_of_time)` exposes typed `forecast_point`, `actual`,
`residual_actual_minus_forecast`, `absolute_error`, `squared_error`, and
`interval_covered` series. Its as-of predicate uses the real finalization time,
so a future feature snapshot cannot see an evaluation before PlantGeo actually
recorded it. These signals are ML feature/evaluation inputs, not operational
forecast publications.

Revision `20260722_0007` additionally requires the complete empirical
calibration horizon to end at or before the simulated cutoff; an earlier
calibration origin alone does not prove the calibration targets are pre-cutoff.

Revision `20260722_0008` requires every new hindcast to enter as staged v2 and
rejects direct finalized or explicit-v1 inserts. It requires an active parent
quality policy and at least `min_backtest_points` actual residual-calibration
samples before a staged hindcast can finalize. The finalizer preserves the legacy
expected-horizon gate only for v1 idempotent verification and uses the
residual-band sample count for v2. It also versions the hindcast receipt digest. The 14
retained receipts are explicitly `hindcast_v1` and continue to use their exact
original formula. New `hindcast_v2` receipts additionally bind the forecast run,
series and release identities; stable series/model/policy keys and IDs; both
cutoffs; horizon, interval, training and expected counts; and each actual's
availability timestamp alongside its immutable value checksum. V2 also binds a
canonical policy-definition contract containing activation, sample minimums,
metric thresholds, coverage, skill, and quantiles. A policy referenced by a
finalized receipt cannot be updated or deleted; changed thresholds require a new
policy identity. The outcome view exposes the digest version and policy contract
so an external verifier never guesses either formula.

## First local evaluation evidence (2026-07-22)

The first target is the exact NASA POWER `WS2M` wind-speed point sample at
40°N, 105°W. It is a defensible baseline candidate because the validated,
release-pinned history contains 1,462 consecutive daily observations from
2022-04-30 through 2026-04-30, has no gap greater than one day, retains the
native 55,660-metre point-sample support, and varies from 0.59 to 8.05 m/s. It
is simple enough to audit with elapsed-time SQL regression while still having
real temporal behavior.

The terminal seven-day holdout metrics satisfied every standalone policy
threshold: MAE
`0.5293168554`, RMSE `0.6248889318`, naive RMSE `1.6827783149`, skill
`0.6286564153`, bias `0.4277564577`, MAPE `0.2970724300`, and coverage `1.0`.
The broader deterministic rehearsal persisted 13 four-week-spaced origins plus
the terminal cutoff (14 total), for 98 forecast-versus-actual points spanning
2025-05-08 through 2026-04-30. Only 3 of 14 origins passed individually. Its
aggregate MAE was `0.7664812881`, RMSE `1.0076008331`, naive RMSE
`1.3054340900`, skill `0.2281488274`, MAPE `0.3531289996`, point coverage
`1.0`, and empirical p10-p90 interval coverage `0.6326530612`.

The aggregate MAE, RMSE, and MAPE exceeded the reviewed policy, so forecast run
`598466ea-1181-4772-8f30-f46574dce1e9` is immutably `rejected`. All 14
hindcast receipts remain queryable; their one-receipt-per-origin manifest is
`21f5c127e0084ec1f7501c096c90169e57487bec514ec57cfa43c279c38c40e8`.
There are zero operational receipts, values, or publications. The favorable
terminal window was not used to override the representative historical gate.
The corrected fixture also requires at least half of historical origins to pass
individually and at least 0.70 empirical p10-p90 interval coverage; the retained
v1 result would fail both of those additional stability/calibration gates.

The retained v1 parent is explicitly preflight evidence, not a valid
operational chronology: its planned `issue_time` was 2026-07-22 01:43Z and its
actual row creation began at 12:35Z. Because aggregate rejection occurred
before `validate_forecast_run`, the immutable terminal metric's stored `passed`
flag remains false even though its values satisfy the standalone thresholds.
Canonical job output `c19a3747-4a14-48a4-ae4c-b3ff3bfcd58e` records those two
facts, the 14-receipt manifest, the legacy value-expanded digest, and that no
operational publication was created. Future fixture executions use their real
statement start rather than a hard-coded planned issue time.

## Corrected v2 local evaluation (2026-07-23)

The corrected fixture reuses the governed exact-source series because its
source/support identity is unique and must not be duplicated or relabeled. It
creates new v2 model, policy, candidate run, and `hindcast_v2` receipt
identities. The fixture is hard-wired to evaluation-only mode: failure retains
the rejected evidence, while success would validate the run and return before
creating forecast receipts, forecast values, publications, or pointers. A
second constant authorization guard independently blocks all receipt and
publication DML if evaluation-only mode is ever changed.

Disposable run `3a3723bc-a039-4b22-b564-25ebd4edbf57` recomputed all 14 origins
and 98 outcomes under the v2 digest. The deterministic metrics matched the
retained evidence: 3/14 origins passed and empirical interval coverage was
`0.6326530612`, below the required `0.50` origin pass fraction and `0.70`
coverage thresholds. The run is therefore `rejected`; its v2 receipt manifest
is `dc787e726acb6ba81e5a7d0a3361d8b57f8457d794add74bdc906d59c3bc444c`.
No forecast receipt, value, publication, or pointer was created.

## Generic 30-day iteration pipeline

Revision `20260723_0010` adds a separately governed evaluation plane for the
view/procedure architecture:

1. `v_forecast_timeseries_contract` registers provider/licence, entity, metric,
   unit, spatial support, native/output resolution, and desired grain.
2. `forecast_timeseries_contract(release_set, as_of)` supplies only governed
   observations visible at the requested availability boundary. A compact
   server-written high-water ledger records source-release content changes,
   release-set membership, and contract metadata changes without adding one
   ledger row per observation.
3. `forecast_date_spine` and `forecast_aligned_daily_series` create UTC calendar
   days without collapsing gaps. Strict mode preserves missing days; LOCF may
   bridge only one immediately preceding raw day. Each row retains source release,
   observation digest, availability, imputation status, and an alignment checksum.
4. `forecast_daily_bootstrap` draws deterministic historical daily increments
   using SHA-256-derived indices and emits low/p10, median/p50, and high/p90 paths.
5. `materialize_forecast_iteration` persists a checksummed, immutable,
   `evaluation_only` iteration and its daily values, including the canonical
   contract JSON and exact training-release license snapshots.
6. `reconcile_forecast_iteration_actuals` uses a separately supplied later
   validated release set to append actual values and exact contributing input
   lineage. A full UTC bucket must be closed and have the expected native
   sample count; v2 actual digests bind release-set identity, manifest,
   observation checksums, and persisted source-release license snapshots.
7. `v_forecast_iteration_outcome` and
   `forecast_iteration_signal_timeseries` expose forecast, actual, residual,
   forecast error, absolute/squared error, and interval coverage only after the
   relevant database recording time.

The default horizon is 30 days and the default simulation count is 1,000, with a
hard range of 100–10,000. The release manifest, full aligned-history checksum,
contract, cutoff/as-of/history window, seed, simulation count, gap policy,
bounds, and ordered values are digest-bound. Stable iteration keys make reruns
idempotent; changing immutable parameters or evidence under the same key fails.
Digest functions pin UTC, date, interval, and floating-point rendering settings
so client session locale cannot change a receipt.

This v1 method is an empirical independent-increment bootstrap. It does not model
seasonality, autocorrelation, regime change, or cross-series dependence. Its
p10/p50/p90 values are empirical path quantiles, not calibrated confidence or
life-safety bounds. Coarse spatial support stays coarse, registered support must
agree with the governed source crosswalk, daily output is rejected for source
temporal support coarser than one day, and subdaily sources require explicit
mean aggregation in v1. Retrospective pinned-release iterations are labeled as
such. Later methods and corrected actuals must create new immutable versions
rather than changing prior evidence.

Nothing in the iteration plane joins `forecast_publication_item`, advances a
publication pointer, schedules a job, or creates a strategy/recommendation.

### Refresh, regeneration, and publication

These are deliberately separate operations:

- `forecast_timeseries_contract`, `forecast_rolling_stats`,
  `forecast_linear_*`, and `forecast_daily_bootstrap` compute pinned,
  as-of-honest values on demand.
- `materialize_forecast_iteration` creates a new immutable evaluation-only
  iteration when an operator chooses a newer release/cutoff; it never rewrites
  an earlier iteration.
- `reconcile_forecast_iteration_actuals` binds later actuals, while
  `v_forecast_iteration_outcome`, `forecast_iteration_evaluation`, and
  `forecast_iteration_signal_timeseries` expose the scored evidence on demand.
- `publish_forecast_publication` is the separate governed promotion step.
  `v_forecast_series_serving` exposes only published receipts.
- `refresh_forecast_ml_daily_serving` explicitly refreshes the published ML
  materialized view. Source ingestion does not silently refresh, retrain, or
  publish a model.

## Strategy-selection boundary

The intended ML task is not merely value extrapolation: it selects or ranks a
governed `agri.strategies` candidate for a projected problem area using the
historical entity-state and feature snapshot that produced the forecast. The
selection receipt binds the problem forecast or evaluation iteration, feature
snapshot, validated local training receipt, strategy version/evidence, issue
and applicability times, label release, and selection-policy checksum. The
normalization, estimator ladder, hard gates, and abstention rules are defined
in [strategy-selection-training.md](strategy-selection-training.md).

Label tables and model execution do not by themselves authorize an effect
claim. Until the label release and causal policy pass independent review,
outputs may be labeled only as `feasibility_candidate`. They are not causal effect estimates,
prescriptions, or permission to act.
The focused requirements interview is retained in the versioned label,
outcome, counterfactual, spatial-transfer, evaluation, review, and abstention
contracts described in [strategy-selection-training.md](strategy-selection-training.md).

## PostgreSQL implementation plan

1. Add a forward-only Alembic revision and matching SQLAlchemy metadata for normalized series, entity state, feature/model lineage, quality policies and metrics, immutable receipts/values, and atomic publications.
2. Add PostgreSQL functions for bounded rolling statistics, percentiles, irregular-time linear regression, holdout backtesting, receipt finalization, publication, and explicit materialized-view refresh.
3. Add serving views that filter to published, validated receipts and keep raw/native versus aggregate spatial and temporal support visible.
4. Validate schema invariants without data, then exercise the PostgreSQL functions against synthetic test-only series inside a rolled-back test transaction. Do not load or alter the retained NASA POWER or USDM history.
5. Keep scheduling and Railway projection disabled until local source coverage, backtests, immutable receipt validation, promotion receipts, and a reviewed runtime role are proven.

## Quality and honesty gates

- A source release and release set must already be validated, time-pinned, and checksum-bound.
- Regression requires enough distinct timestamps and a non-zero time denominator; backtests record sample count, MAE, RMSE, bias, optional MAPE, a last-value baseline, and skill before run validation.
- Percentile bands use empirical held-out residual quantiles and are stored as declared quantiles, not mislabeled fixed standard-deviation offsets.
- ML storage is available, but ML execution remains `gated` until a validated local training run supplies model, training-code, feature, and input-release checksums.
- North America coverage is represented only by the actual source/cell support of each series. Coarse grids, point samples, polygons, and aggregates remain coarse grids, point samples, polygons, and aggregates.
- The existing hot-projection manifest is a selection contract only and is never accepted as source, model, backtest, or forecast evidence.
