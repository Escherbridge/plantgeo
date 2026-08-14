---
type: pre-registration
---

# Pre-registration: seasonal candidate ladder on the frozen Boise-area export

Written **2026-08-14, before any candidate was scored**. Everything below is fixed at the moment of
writing; a later change is a new pre-registration with a new date, not an edit to this one.

Evidence this rests on: `evidence-phase0-2026-08-14.md` (read-only, same day).

## 0. Frozen corpus

- Export: `.agri-local-runs/seasonal-eval-export-2026-08-14/`, format `seasonal-eval-export-v1`.
- Cells (NASA POWER 0.5-degree analysis lattice, nearest-first to Boise 43.6150 N / 116.2023 W):
  `na-sample:1deg:p044.00:m116.00` (45.8 km), `na-sample:1deg:p043.00:m116.00` (70.3 km),
  `na-sample:1deg:p044.00:m117.00` (77.1 km), `na-sample:1deg:p043.00:m117.00` (94.1 km).
- Observation window: 2022-04-30 through 2026-08-06 inclusive, 1,560 calendar days, measured
  gap-free on every NASA POWER series at all four cells.
- Deduplication: one row per (cell, signal, support, UTC day), `ORDER BY data_available_at DESC,
  id DESC`. Recorded in the manifest, not applied ad hoc at scoring time.
- Source release identity, licence-snapshot digest, validation state and per-series
  `data_available_at` bounds are recorded in `manifest.json`; the manifest's own SHA-256 is the
  identity every result binds to.

## 1. Availability honesty — stated before, not after, the numbers

Measured: the minimum `data_available_at` over every profiled Boise-area series is **2026-08-05**,
while observations run back to 2022-04-30. The whole history became warehouse-visible in a single
recent window.

Therefore this evaluation is pre-registered as **observation-time honest and not revision honest**:

- Enforced: no value whose `observed_date` is on or after an origin cutoff may enter that origin's
  training, scaling, imputation, season definition, hyperparameter choice or calibration. This is
  checked mechanically per fold and reported as `leakage_checked_rows`.
- Not enforced, and not claimable: point-in-time correctness with respect to source revisions. A
  2024 origin reads the 2026 version of a 2024 observation. NASA POWER reprocesses. No result here
  may be quoted as an operational or life-safety skill estimate.

## 2. Target series

Two signals at each of the four cells — eight target series:

| signal | unit | why |
| --- | --- | --- |
| `wind_speed` | m/s | the series the track spec names (NASA POWER WS2M at Boise) |
| `air_temperature_mean` | C | a strongly seasonal target, without which a seasonal ladder cannot be discriminated from a persistence ladder |

## 3. Rolling origins (fixed now)

- History start 2022-04-30; last observed day 2026-08-06.
- Minimum training history before the first origin: **730 days** (two full seasonal cycles).
- First origin cutoff: **2024-04-29**. Stride: **30 days**. Horizon: steps **1..30** (target days
  `origin + 1 .. origin + 30`), so consecutive origins have exactly **non-overlapping** target
  windows.
- Last origin whose target window closes on or before 2026-08-06: **2026-07-07**.
- **Expected 27 origins.** Split, fixed now:
  - **development**: origin cutoff < 2025-08-06 → expected **16** origins.
  - **final holdout**: origin cutoff >= 2025-08-06 → expected **11** origins, scored exactly once,
    after every candidate and hyperparameter is frozen on development origins.
- Fitting rule: expanding window. Every origin fits on all export rows with
  `observed_date < origin cutoff` and nothing else.

## 4. Candidate ladder (fixed now)

All are database-free cores in `src/agri_data_service/method/ml/seasonal_candidates.py`, all
deterministic, all seeded with `20260814`.

| candidate | family | fitted inside each fold | interval source |
| --- | --- | --- | --- |
| `persistence_v1` | baseline | last observed value before the cutoff | held-out calibration residuals |
| `seasonal_naive_v1` | seasonal baseline | value 365 days before the target day, nearest within +/-3 days | held-out calibration residuals |
| `seasonal_climatology_v1` | seasonal baseline | circular day-of-year mean over a +/-15-day window, minimum 5 samples | held-out calibration residuals |
| `sql_linear_elapsed_time_v1` | the shipped SQL-linear baseline, reimplemented | ordinary least squares of value on elapsed UTC seconds since history start | held-out calibration residuals |
| `daily_increment_bootstrap_v1` | the shipped bootstrap baseline, reimplemented | consecutive-calendar-day first differences, resampled with SHA-256-derived deterministic indices, 2,000 paths | native simulated path quantiles |
| `regularized_lag_seasonal_ridge_v1` | the new candidate | direct per-horizon ridge on standardized lag, rolling-mean and harmonic seasonal features | held-out calibration residuals |

`sql_linear_elapsed_time_v1` is a literal reimplementation of the warehouse's `sql_linear` method
(regression on elapsed UTC time with empirical holdout residual bands), not a ridge wearing that
name. The regularized lag/seasonal candidate is the separate, new family.

Ridge feature set, fixed now: lags of the target series at {1, 2, 3, 5, 7, 14, 21, 28} days before
the origin; rolling means over {7, 14, 30} days ending before the origin; `sin`/`cos` of the target
day-of-year at 1 and 2 cycles per year. Standardization mean/standard deviation are computed on
training rows only. (Amended the same day, still before any candidate was scored: an earlier draft
of this line also listed "the horizon step" as a feature. The candidate fits one model per horizon
step, so that column is constant within a model and degenerate against the intercept. It is
removed rather than fitted; nothing had been run when this was corrected.)

Ridge penalty grid, fixed now: {0.01, 0.1, 1.0, 10.0, 100.0}. Selected **inside each training fold**
by an inner temporal split (last 20 % of training pseudo-origins held out), never by looking at the
scored window.

## 5. Interval calibration

For every candidate without a native interval, the p10/p90 band per horizon step comes from a
**held-out calibration segment inside the training window**: the last 365 days before the origin.
The model is fitted on data preceding that segment, residuals are collected at pseudo-origins spaced
7 days apart inside it, the empirical 10th/90th percentiles per horizon step become the band, and
the model is then refitted on the full training window for the scored forecast. No target day is
ever in the window that produced the band scoring it.

## 6. Metrics (fixed now)

Per (series, candidate, fold kind), and sliced by horizon step and by meteorological season of the
target day (DJF / MAM / JJA / SON):

- **MAE**, **RMSE**, **bias** (mean signed error, forecast minus actual).
- **MAPE**, reported only where every actual in the slice satisfies `abs(actual) >= 1.0` in its own
  unit; otherwise recorded as `null` with reason `mape_undefined_near_zero`. `air_temperature_mean`
  crosses zero, so MAPE is expected to be undefined for it and that is not a failure.
- **Skill versus persistence** = `1 - MAE_candidate / MAE_persistence_v1`, at identical origins.
- **Interval coverage** = fraction of scored target days with `p10 <= actual <= p90`; nominal 0.80.
- **Pass fraction** = fraction of origins whose own MAE is at or below the persistence MAE.

## 7. Uncertainty (fixed now)

Block bootstrap over **whole origins**: resample origins with replacement, keeping all eight series
and all thirty horizon steps of a drawn origin together, because target days inside one origin are
autocorrelated and series at four cells 45-95 km apart are spatially dependent. 2,000 draws, seed
`20260814`, reported as percentile 2.5 / 97.5 intervals on MAE and on skill.

The effective sample size is the **origin count**, never the scored-day count. 27 origins is the
number that qualifies every interval below.

## 8. Abstention thresholds (fixed now)

Recorded as a durable abstention, and that phase stops, if any holds:

1. fewer than **8** development origins are scorable for a series, or
2. fewer than **6** final-holdout origins are scorable for a series, or
3. fewer than **95 %** of the scheduled target days of a fold carry an actual in the frozen export.

An origin is "scorable" when its training window has at least 730 days of observations and its whole
target window carries actuals.

## 9. Acceptance gates (fixed now)

A candidate family is **accepted** only if, on the **final holdout**, all hold:

1. skill versus persistence > 0 **and** the 2.5th percentile of its bootstrap skill interval > 0;
2. interval coverage within [0.70, 0.90] (nominal 0.80 +/- 0.10);
3. no seasonal stratum with skill versus persistence < -0.10;
4. no leakage, support, lineage or calibration failure recorded for the run.

Otherwise **rejected**, unless an abstention threshold in section 8 fired, in which case
**abstained**. A better average with a failing seasonal slice, weak coverage or too few origins is
rejected, not accepted with a caveat.

## 10. What this pre-registration does not authorize

No result here promotes a forecast, writes an operational issue time, enters
`v_forecast_series_serving`, `forecast_publication`, `forecast_publication_item`, a receipt, a
recommendation surface, or a Railway service. The persistence plane built for this track is
evaluation-only by construction (`evaluation_only` CHECKed true, `publication_authorized` CHECKed
false, no foreign key to any publication relation).
