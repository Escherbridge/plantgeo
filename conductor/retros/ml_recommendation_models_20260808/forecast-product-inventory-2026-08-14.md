---
type: evaluation-report
---

# Forecast-product inventory (prod, read-only), 2026-08-14

Verified by read-only queries against `DATABASE_URL_SYNC` (`plantgeo`, the production
warehouse), every query wrapped in `BEGIN READ ONLY; ... COMMIT;`. No write of any kind was
issued against this database. Purpose: satisfy plan item "Inventory which forecast products
exist per Boise-area series ... to pick the AnEn pilot metric and the conformal calibration
target from evidence" (Phase 0).

## 1. What is actually live

| plane | rows | finding |
| --- | --- | --- |
| `agri.forecast_series` | 1,568 | **100% NDVI.** Every row is `entity_type='grid_cell'`, `metric_name='ndvi'`, `spatial_support_kind='native_grid_cell'`. Zero rows for `wind_speed` or any other NASA POWER metric. |
| `agri.forecast_iteration` | 1,676 | **100% `ndvi_seasonal_anomaly_bootstrap_v1`.** 1,558 `forward_simulation` + 118 `holdout_evaluation`, all `status='finalized'`. Zero `daily_increment_bootstrap_v1` iterations anywhere, for any series. |
| `agri.forecast_run` | 0 | Empty. No SQL-linear or ML forecast run has ever been staged. |
| `agri.forecast_model` | 0 | Empty. No model (wind ridge, AnEn, or otherwise) has ever been registered. |
| `agri.forecast_training_run` | 0 | Empty. `covariate_wind_persist.py --persist` has never been run against prod. |
| `agri.forecast_backtest_metric` | 0 | Empty, same reason. |
| `agri.forecast_iteration_actual` (via `v_forecast_iteration_outcome`) | 473 rows across 118 iterations | Only the `holdout_evaluation` iterations have actuals recorded (all on 2026-08-05); the 1,558 `forward_simulation` iterations (cutoff 2026-08-05, still forward-looking as of this inventory) have zero actuals so far. |
| `agri.signal_observation` | (not recounted here; ≈46.1M rows per task brief) | NASA POWER meteorology is broad and real: confirmed 11 signals × ~4,386 rows each at the four Boise-area 0.5°-nominal cells (`na-sample:1deg:p043.00:m116.00`, `:p043.00:m117.00`, `:p044.00:m116.00`, `:p044.00:m117.00`), 2022-04-30 → 2026-08-06. |
| `agri.drought_polygon_snapshot` | **0** | **USDM/drought has never been ingested into the production warehouse**, at all, for any cell, any week. Confirmed by both a row count and `ST_Extent` (NULL). |

This corrects an assumption the local Boise-Trekker-Rim evaluation report
(`plans/boise-trekker-rim-covariate-layer-and-first-model-2026-08-02.md`) invited: that report's
"98.09% covariate completion" figure was measured on a **local disposable snapshot**
(`plantgeo_boise_completion_20260725`), which did carry ingested USDM polygons. Production never
received that ingestion pass.

## 2. The consequence for the covariate layer, stated precisely

`agri.covariate_daily_features`'s whole-row completeness mask discards a day the instant **any**
one of the 40 pinned `agri_covariates_v1` features is `NULL` -- and `drought_severity_class_lag_1`
/ `_lag_7` are unconditionally `NULL` everywhere in prod today, because an unresolved drought
class is never coerced to class 0 (`execution/AGENTS.md`, "Assignment-time covariate layer").

Measured directly against the nearest-to-Boise NASA POWER cell
(`bb533220-3fca-410f-b0fa-989cf76806d0`, `na-sample:1deg:p044.00:m116.00`), full 2022-04-30 →
2026-08-06 window:

```
complete_day_count = 0   (of 1,560 candidate days)
complete_row_count = 58,993 / 62,400 emitted rows
partial_row_count  = 3,407
imputed_row_count  = 4,680
```

Every one of those 58,993 "complete" **rows** is a meteorology feature on a day whose **row** is
still incomplete, because the two drought-class columns are NULL on every single day. This is not
specific to that cell -- `drought_polygon_snapshot` is empty for the whole database, so
**`complete_day_count = 0` holds for every cell in prod**, unconditionally.

**Consequence for both ML lanes equally.** `covariate_wind_model.py`'s own `run_direct_multi_horizon`
and this track's `analog_ensemble_model.run_anen_origin` both refuse to score an origin whose row is
incomplete (`OriginNotEvaluableError`). Neither lane can produce a real-data backtest against
prod today -- this is a pre-existing, systemic gap in the covariate layer's production data, not
something introduced by or specific to this track's work. `forecast-train-wind --persist` against
prod would fail identically.

## 3. What this means for the two items this inventory was meant to settle

- **AnEn pilot metric.** `wind_speed` at the Boise-area NASA POWER cell
  (`bb533220-3fca-410f-b0fa-989cf76806d0`) is the correct choice on every axis the plan names --
  it is the one metric with a real, pinned 40-feature covariate vector (`agri_covariates_v1`) and
  therefore the only metric for which "full-vector distance vs. target-lags-only distance" is a
  meaningful ablation -- but the completeness gap in §2 means it **cannot be exercised against
  real prod rows today**. The AnEn backtest evidence for this track is therefore produced two ways,
  recorded in `analog-ensemble-backtest-2026-08-14.md`: (a) a real, durable, receipted run on the
  disposable local warehouse with a seeded meteorology + drought fixture (proving the framework
  and the receipt chain), and (b) this inventory's honest statement that a prod-data backtest is
  currently blocked, not attempted, and not claimed.
- **Conformal calibration target.** NDVI (`ndvi_seasonal_anomaly_bootstrap_v1`) is the only method
  with both a live prod baseline **and** recorded actuals (473 residual rows, 118 iterations,
  6 distinct cutoff times spanning 2025-05-01 → 2026-07-01) -- see
  `conformal-recalibration-ndvi-2026-08-14.md` for the real before/after recalibration run against
  those rows.

## 4. Boise-area NDVI series located for reference

`ndvi-daily:sentinel2-ndvi-0p25deg:43.6250:-116.6250` (centroid ~11 km from downtown Boise) has a
`holdout_evaluation` iteration with 28 recorded actuals -- the closest single-series NDVI example
to the Boise entity this track is chartered around. The recalibration report scores the full
118-iteration / 473-row governed set rather than this one series alone, for statistical power; see
that report for why.

## 5. Method

All queries ran as `BEGIN READ ONLY; SET statement_timeout = '60s'; ...; COMMIT;` via `psql`
against `DATABASE_URL_SYNC`. No `INSERT`/`UPDATE`/`DELETE` was issued. Full query text is not
reproduced here; the counts above are the ones a reviewer would reproduce by re-running the
`SELECT count(*)` / `covariate_vector_manifest` calls named in each row.
