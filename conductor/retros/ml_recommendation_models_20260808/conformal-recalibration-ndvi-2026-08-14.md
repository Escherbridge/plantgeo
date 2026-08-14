---
type: evaluation-report
---

# NDVI split-conformal recalibration: before/after interval coverage (prod, read-only)

Dated 2026-08-14. Real numbers, computed by
`agri_data_service.execution.conformal_recalibration.run_recalibration` reading
`agri.v_forecast_iteration_outcome` through the new
`sql/execution/select_forecast_iteration_residuals.sql`, against `DATABASE_URL_SYNC` (prod). The
session issued `SET TRANSACTION READ ONLY` and rolled back; nothing was written. This report
proves the split-conformal calibration loop runs end to end on real recorded residuals and
measures a real coverage improvement; it does **not** certify the recalibrated band as an
operational or life-safety-valid interval -- see §4.

## 1. What was calibrated

Method: `ndvi_seasonal_anomaly_bootstrap_v1` (the only live forecast product in prod; see
`forecast-product-inventory-2026-08-14.md`). Scope: every governed NDVI series with a
`holdout_evaluation` iteration that has recorded actuals -- 118 iterations, 473 (iteration,
horizon-step) residual rows, spanning six distinct cutoff (origin) dates:
`2025-05-01, 2025-07-01, 2025-09-01, 2026-03-01, 2026-05-01, 2026-07-01`.

## 2. The split (by origin, not by row)

| fold | cutoffs included | distinct origins | residual rows |
| --- | --- | --- | --- |
| calibration | `2025-05-01, 2025-07-01, 2025-09-01, 2026-03-01` | 4 | 276 |
| held-out | `2026-05-01, 2026-07-01` | 2 | 197 |

The boundary (`2026-05-01`) sits at the one real gap in the cutoff-time distribution: calibration
uses strictly older origins, held-out uses strictly newer ones -- a genuine temporal split, not an
arbitrary count-based one. Every horizon step of one iteration stays in the same fold
(`split_by_origin` partitions by `cutoff_time`, never by row), so no origin's own forecast
partially trains the margin it is later scored against.

Both folds are independently availability-gated: a residual is admissible only when both
`forecast_available_at` and `actual_recorded_at` are at or before the run's `as_of_time`
(2026-08-14, the moment this report ran). All 473 rows cleared that gate -- every recorded NDVI
actual in prod today was already available.

## 3. Real before/after result

```
nominal_coverage          = 0.80
calibration_sample_count  = 276
held_out_sample_count     = 197
conformal_margin          = 0.05924660815164484   (NDVI units, index range [-1, 1])
before_coverage           = 0.6700507614213198     (67.0%)
after_coverage             = 0.8984771573604061     (89.8%)
digest                     = 64bb1fc7bc7fb2611780636229f2b995c99061b965aaa07ff9eef5d94e80e98a
```

| band                                | half-width (NDVI units) |
| --- | --- |
| nominal (before), mean over the 197 held-out rows | 0.0419 |
| nominal (before), min / max over the 197 held-out rows | 0.0081 / 0.1168 |
| conformal (after), uniform per row | 0.0592 |

The conformal band is **41% wider on average** than the nominal one (0.0592 vs. a mean 0.0419
half-width), so the 67.0% → 89.8% coverage gain is not free: it costs real width, and the min/max
spread shows the nominal band's own width already varies more than 14x row to row (0.0081 to
0.1168) while the conformal band replaces that variation with one fixed width. Computed by the
same read-only path as §3's headline numbers -- a fresh re-run on 2026-08-14 against
`DATABASE_URL` (prod) reproduced the identical `calibration_sample_count`, `held_out_sample_count`,
`conformal_margin`, `before_coverage` and `after_coverage`, confirming no new NDVI actuals landed
between the two runs and that the widths above describe the exact same 197 held-out rows the
headline coverage numbers were scored against.

**`before_coverage`** is the held-out fold's hit rate against each iteration's own recorded
p10-p90 band (the live, uncalibrated nominal band). **`after_coverage`** re-scores the *same*
197 held-out rows against `median ± 0.0592`, where the margin was fit *only* from the 276
calibration-fold residuals.

**This independently confirms the under-dispersion this method's own documentation already
states.** `execution/AGENTS.md` (`vegetation_ndvi_forecast.py`) says: "Measured holdout interval
coverage is well below the nominal 80%, so the band is under-dispersed and must be presented as
indicative, not as a calibrated prediction interval." The measured 67.0% here is that same claim,
now demonstrated against real held-out prod origins rather than asserted. Split-conformal
recalibration closes most of the gap -- 67.0% → 89.8% against an 80% target -- overshooting
slightly rather than undershooting, which is the expected behavior of a margin fit on a smaller,
noisier calibration sample (n=276).

## 4. What this does and does not prove

- **Proves:** the split-conformal loop is wired to real recorded `forecast_iteration_value` /
  `forecast_iteration_actual` rows, the availability gate is real (not a no-op), the calibration
  and held-out folds are genuinely disjoint by origin, and recalibration measurably improves
  coverage on real held-out prod data.
- **Does not prove:** that 89.8% is a *calibrated* interval in the distributional sense, that it
  generalizes past this one origin split, or that NDVI seasonal-anomaly forecasts are life-safety
  valid. `n=2` held-out origins is a small sample for a coverage estimate; treat 89.8% as a
  measured point, not a guaranteed future rate. This is evaluation evidence for the ML track, not
  a publication-surface claim -- it never joins `v_forecast_series_serving` or any recommendation
  path.

## 5. Reproduction

```
uv run python -m agri_data_service.execution.analog_ensemble_cli forecast-recalibrate-ndvi \
  --method ndvi_seasonal_anomaly_bootstrap_v1 \
  --calibration-cutoff-before 2026-05-01 \
  --held-out-cutoff-at-or-after 2026-05-01 \
  --nominal-coverage 0.80
```

(Command shown once the orchestrator registers `forecast_recalibrate_ndvi` in `cli.py`; see the
track's implementation report for the exact registration line. The numbers above were produced by
directly invoking `agri_data_service.execution.conformal_recalibration.run_recalibration` against
`DATABASE_URL_SYNC` with the same parameters, since the CLI registration itself is out of this
track's file boundary.)
