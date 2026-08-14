---
type: evaluation-report
---

# Analog Ensemble (AnEn): receipt-chain proof and backtest evidence, 2026-08-14

Two separate pieces of evidence, kept explicitly separate because they answer different
questions. **Neither is an operational forecast or life-safety validated; both are evaluation
evidence for the ML track.**

## 1. Why there is no prod-data AnEn backtest here

`forecast-product-inventory-2026-08-14.md` §2 establishes that `agri.drought_polygon_snapshot`
has **zero rows** in production, so `agri.covariate_daily_features`'s whole-row completeness mask
(`complete_day_count`) is **0 for every cell in prod**, unconditionally. `analog_ensemble_model
.run_anen_origin` refuses to score an origin whose covariate row is incomplete
(`OriginNotEvaluableError`), exactly mirroring `covariate_wind_model.run_direct_multi_horizon`'s
own refusal. Attempted directly against the Boise-nearest NASA POWER cell
(`bb533220-3fca-410f-b0fa-989cf76806d0`, six rolling origins, 2026-02 → 2026-07): every origin
refused with `"has an incomplete covariate vector"`. This is a pre-existing, systemic production
data gap, not something this track's code introduced or can route around without either (a) a
real USDM ingestion pass into prod (an ingest-lane job, out of this track's scope and read-only
mandate) or (b) a new covariate schema version that drops the drought features (out of scope:
`agri_covariates_v2` is `covariates_v2.py`, explicitly off-limits to this track).

So: the metrics below prove the AnEn *framework* runs end to end, including a real receipt chain
on a real PostgreSQL server. They are evaluation evidence that the machinery works, not a claim
about Boise wind-speed forecast skill on real historical data.

## 2. Disposable-database proof: real receipt chain, real MAE/RMSE, real analog search

Run against `agri_sweep` (podman `agri-sweep-db`, port 5442, Alembic head `20260808_0019` --
migration `20260814_0020` was **not** applied; see §4). Fixture: one synthetic spatial cell, 90
days of deterministic (seeded, non-random) meteorology across all seven `agri_covariates_v1`
signals, weekly-cadence covering USDM polygons so the whole-row completeness mask can resolve (see
`tests/test_analog_ensemble_persist_postgresql.py` for the exact seed), one validated
`release_set`, one active `forecast_quality_policy`. `k_neighbors=3`, `temporal_exclusion_days=5`,
`horizon_days=5`, single origin (`2024-03-26`).

```
feature_coverage.candidate_day_count  = 90
feature_coverage.complete_day_count   = 62   (first 28 days incomplete: no 28-day rolling lookback yet)
full_vector.origins[0].analog_pool_size        = 52
full_vector.origins[0].mean_analog_count       = 3.0
full_vector.aggregate.evaluated_count          = 4     (horizon step 5's target date fell outside the 90-day window)
full_vector.aggregate.mean_absolute_error      = 0.8059848109788346
full_vector.aggregate.root_mean_squared_error  = 0.8340660775286141
full_vector.aggregate.interval_coverage        = 0.0
full_vector.pooled_naive_root_mean_squared_error = 0.340060768690697
full_vector.origins[0].skill_score             = -1.4526971480419153   (AnEn worse than naive persistence, on this synthetic fixture)
```

**The full-vector and target-lags-only ablation produced numerically identical results on this
fixture** -- expected and disclosed, not a bug: the synthetic meteorology signals share one
sinusoidal phase across all seven variables, so their relative day-to-day distances are highly
collinear. A real ablation answer needs real, non-collinear meteorology, which is exactly what the
prod completeness gap in §1 currently blocks. This fixture proves the ablation *mechanism* wires
correctly (two independently-scored variants, two feature masks, one shared origin); it does not
answer the ablation *question*.

### Real, durable receipt ids (two independent commits, both independently re-queried afterward)

| run | training_run_id | forecast_run_id | model_id | training_run_status |
| --- | --- | --- | --- | --- |
| 1 | `b0a76e03-78be-45e2-b905-4ffca31ae745` | `35e03c52-8109-4bdb-9ad9-1020e6ef4f94` | `24488d62-8422-4e25-8be0-7d07fa7d7bbb` | `validated` |
| 2 | `184e5c13-e38c-40fd-b03d-40f370ceae14` | `5640a482-f66b-4077-b635-eded49e69c65` | `ee39dad3-5a00-4aec-9e2c-773593ebd5ff` | `validated` |

Verified independently after commit (fresh `psql` connection, not the writing session):

```sql
SELECT tr.status, tr.training_key FROM agri.forecast_training_run tr
WHERE tr.id = '184e5c13-e38c-40fd-b03d-40f370ceae14';
--        status   |                                   training_key
-- -----------------+------------------------------------------------------------------------
--  validated | analog-ensemble:e632dd16-69f3-4f2b-babc-3a1bd315d394:2024-03-26:9b8c132e287ba2cd
```

`SELECT count(*) FROM agri.forecast_training_run` on `agri_sweep` reads `2` (both runs, both
`validated`), against `0` in prod. The chain writes `agri.job_definition`, `agri.job_run`,
`agri.artifact` (inline model document), `agri.forecast_model` (`model_kind='ml'`,
`algorithm='analog_ensemble_v1'`), `agri.job_output` (×2), `agri.forecast_feature_snapshot`
(`validated` via `agri.validate_forecast_feature_snapshot`), `agri.forecast_training_run`
(`validated` via `agri.validate_forecast_training_run`), `agri.forecast_run`
(`status='staged'`, never promoted), and one `agri.forecast_backtest_metric` row per origin. It
writes **no** `forecast_receipt`, `forecast_value`, or publication row -- the same structural
non-leakage guarantee `covariate_wind_persist.py` establishes (the absence of a row, not a
`WHERE` clause).

### A real defect found and fixed during this proof

The first of the two runs above was preceded by a run that **failed** with
`"local training validation output lineage mismatch"`. Root cause: `analog_ensemble_model
.model_document` originally recorded only hyperparameters and *structural* per-origin facts
(origin date, analog-pool size, scored-day count) -- no data-dependent content, because AnEn is
non-parametric and has no fitted coefficients the way the wind ridge does. Two runs over
**different** governed cells that happened to share the same origin date, pool size and scored-day
count therefore produced an **identical** `model_document`, hence identical `model_checksum` and
`model_version`, hence `agri.forecast_model`'s `(model_key, model_version)` uniqueness resolved
the second run's model row to the first run's -- while the second run's `job_output` still pointed
at its own, different artifact, and `agri.validate_forecast_training_run`'s lineage check correctly
refused the mismatch. Fixed by binding `cell_id` and the covariate `feature_checksum` into the
document (`analog_ensemble_model.py`, `model_document`); both runs above are post-fix and both
`validated`. This is recorded here as a real found-and-fixed governance-layer catch, not
retroactively smoothed over.

## 3. Vs. `daily_increment_bootstrap_v1`: not computable today, stated honestly

The plan asks for a backtest "against `daily_increment_bootstrap_v1` at identical origins."
Per the inventory, prod has **zero** `daily_increment_bootstrap_v1` iterations for any series
(the method has never run against wind_speed, and no `forecast_series` is registered for it
either). `analog_ensemble_model.evaluate_analog_ensemble` calls the same `load_baseline_evaluation`
reader `covariate_wind_model.py` uses (`agri.forecast_iteration_evaluation`); passed a real
`series_id` it would legitimately return an empty list for exactly this reason. The comparator
this evidence actually carries is the **persistence/naive baseline**
(`AnEnOriginRun.naive_root_mean_squared_error` / `.skill_score`), computed identically to
`covariate_wind_model.py`'s own naive comparator, on the disposable-database fixture in §2. No
number in this report claims to have exercised the governed SQL bootstrap function against a live
series, because it structurally could not be.

## 4. Migration `20260814_0020`

Not applied to `agri_sweep` or prod, and not needed by this track: `analog_ensemble_v1` persists
under the **existing** `model_kind='ml'` / `forecast_method='ml'` values (which already require
`training_run_id IS NOT NULL`, satisfied here), and `forecast_model.algorithm` is an unconstrained
`varchar(150)` that already accepts `'analog_ensemble_v1'` with no schema change. `0020` widens the
CHECKs to admit a separate `'ensemble'` value for a different, NWP-ensemble forecast method this
track does not touch. Left exactly as found.

## 5. Tests

`tests/test_analog_ensemble.py`, `tests/test_analog_ensemble_model.py` (includes the leakage-
boundary proof: an analog whose successor path would reach the origin is never selected, even when
it is the objectively nearest match by raw feature distance -- see
`test_run_anen_origin_never_selects_an_analog_whose_successor_would_leak_past_the_origin`),
`tests/test_analog_ensemble_persist.py` (fake-session receipt-chain shape/order proofs),
`tests/test_analog_ensemble_persist_postgresql.py` (the real-server proof mirrored from §2, rolled
back on teardown -- verified independently: `agri_sweep`'s `forecast_training_run` count stayed at
2 after this test ran, and its seeded `spatial_cell` rows left zero trace).
