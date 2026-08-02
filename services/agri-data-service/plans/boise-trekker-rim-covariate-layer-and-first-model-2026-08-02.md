---
type: evaluation-report
---

# Trekker Rim (Boise) — assignment-time covariate layer and first ML slice

Authored 2026-08-02. Companion to
`boise-trekker-rim-completion-contract-2026-07-25.md`; it uses that contract's
entity, spatial cell, release sets and forecast series unchanged.

> **This is pipeline/evaluation evidence, not a forecast product.** Nothing here
> is operational, life-safety-validated, published, promoted, or joined to a
> serving surface. Every new database object is evaluation-only by construction
> (see §5). The reported MAE/RMSE/coverage prove the framework runs end to end
> on real governed data; they do not certify a forecast.

## 1. The completion gap being closed

| dimension | value |
| --- | --- |
| entity | Trekker Rim parcel `R0541500060`, City of Boise Parks & Rec, Idaho |
| spatial support | analysis cell `boise-local:trekker-rim:p43.556:m116.132`, grid `nasa-power-0.5-degree`, `resolution_m` 55660 (~2244 km²) |
| temporal grain | daily, UTC |
| streams in | NASA POWER daily (7 signals), USDM weekly D0–D4 polygons, deterministic calendar |
| stream declared absent | ERA5-Land (credential-gated) |
| window | 2022-07-23 → 2026-07-23 inclusive (1462 days) |
| metric modelled | `wind_speed` (NASA POWER `WS2M`, m/s) |

Before this work the warehouse held governed **observations** and a governed
**statistical forecast plane**, but nothing in between: there was no
leakage-checked, provenance-carrying surface that turns those streams into an
ML feature/covariate vector. "Complete" for this layer is measured as
`complete_day_count / day_count` from `agri.covariate_vector_manifest` — a day
is complete when **all 40** pinned features resolved every one of their required
inputs.

**Measured completion:** 1434 / 1462 days = **98.09 %**. The 28 incomplete days
are exactly the first 28 of the window, where the 28-day rolling means have no
lookback. That is a structural edge, not a data gap; it is reported as partial
rather than back-filled.

Row-level: 58 480 emitted rows, 58 179 complete, 301 partial, 1794 flagged
`is_imputed` (drought carry-over beyond the USDM weekly cadence).

## 2. D1 — the covariate layer (revision `20260802_0016`)

Four functions, all authored in `db/agri/functions/` and forward-loaded by
`alembic/versions/20260802_0016_assignment_time_covariate_layer.py`.

### 2.1 `agri.covariate_feature_schema(p_schema_version)`

`RETURNS TABLE(feature_index integer, feature_name text, feature_kind text,
stream_key text, signal_name text, lag_days integer, window_days integer)`,
`LANGUAGE plpgsql IMMUTABLE`.

The single pinned, ordered, contiguous covariate vector layout, following the
`0013` label-release precedent that a trainer input must have a *pinned ordered
feature-name schema* rather than whatever column order a query happened to
produce. `agri_covariates_v1` has **40** features:

- indices 1–35: the 7 NASA signals (alphabetical) × 5 shapes
  (`lag_1`, `lag_2`, `lag_3`, `roll_mean_7`, `roll_mean_28`);
- 36–38: `drought_severity_class_lag_1`, `drought_severity_class_lag_7`,
  `drought_severity_imputed_lag_1`;
- 39–40: `day_of_year_sin`, `day_of_year_cos`.

An unrecognised schema version **raises**; it does not silently return zero
features. `agri.covariate_lookback_days(p_schema_version)` derives the maximum
lookback (`max(lag_days + window_days - 1)` = 28) from this one schema, so the
feature reader and the manifest cannot drift out of sync on their window.

### 2.2 `agri.covariate_declared_gap(p_schema_version)`

`RETURNS TABLE(stream_key text, gap_kind text, gap_reason text)`. One row today:
`era5_land` / `credential_gated`, with the reason spelled out. ERA5 is therefore
carried as a *named declared gap in the manifest*, never as empty covariate
columns implying the stream was queried and came back empty. A test asserts no
covariate name matches `%era5%`.

### 2.3 `agri.covariate_daily_features(p_cell_id, p_window_start, p_window_end, p_as_of_time, p_schema_version)`

One row per (cell, UTC day, feature):
`cell_id, observed_date, feature_index, feature_name, feature_kind,
feature_value, input_count, expected_input_count, is_imputed,
source_release_count, data_available_at`. `LANGUAGE sql STABLE`, GUC-pinned
(`TimeZone`, `DateStyle`, `extra_float_digits`), ordered by
`(observed_date, feature_index)`.

Day spine: `agri.forecast_date_spine` (the existing house helper — no second
date-generation idiom), cross-joined against spatial-cell existence so an
unknown `p_cell_id` returns zero rows rather than a grid of all-NULL features.
Drought: `agri.drought_class_daily_series` (the existing canonical USDM daily
resolution — not re-implemented).

The observation CTE takes `DISTINCT ON (signal_name, UTC day)` ordered by
`data_available_at DESC` with a deterministic tie-break. This matters because
`uq_signal_observation_release_cell_signal_time` includes `source_release_id`,
so a re-ingest or a revision legitimately yields several admissible rows for one
`(cell, signal, observed_at, support_key)`. Without the dedupe a rolling window
counted **rows**, not distinct days: a 28-day window holding 26 distinct days,
2 duplicated and 2 missing, returned a mean over the duplicated set while
reporting `input_count 28 / expected 28 / is_imputed false` — silently wrong and
marked clean. Completeness now counts distinct days.

### 2.4 `agri.covariate_vector_manifest(...)`

One row per window: the pinned `feature_names text[]`, `declared_gaps jsonb`,
`source_release_ids uuid[]`, the completion tallies above,
`max_data_available_at`, and a `manifest_checksum` over all of it
(`agri_covariate_vector_manifest_v1`, GUC-pinned, ISO-UTC-formatted
timestamps). This is what a trainer or a future selector pins to bind its inputs.

## 3. Why this is time-honest

1. **Strict lagging.** Every meteorology and drought feature has
   `lag_days >= 1`. A feature row dated D contains **no** day-D observation at
   all. The two calendar features are deterministic functions of D's own
   calendar date and carry no source lineage (`source_release_count = 0`,
   `data_available_at = NULL`). This is what makes the layer simultaneously
   valid as a day-D *assignment-time* covariate vector and as trainer input for
   a day-D target.
2. **The availability filter is a server-recorded timestamp.** Meteorology is
   gated on `signal_observation.data_available_at <= p_as_of_time`; drought
   inherits `drought_polygon_snapshot.data_available_at <= p_as_of_time` from
   `drought_class_daily_series`. The simulated cutoff is never used as an
   availability filter anywhere in this plane. A contract test proves a feature
   whose only input becomes available later is **absent** (NULL value,
   `input_count = 0`, `data_available_at = NULL`) at an earlier `p_as_of_time`
   and present afterwards.
3. **Partial stays partial.** A rolling mean whose window is missing any input
   returns `NULL` with `input_count < expected_input_count` — never a mean over
   the survivors. An unresolved drought class stays `NULL`; it never becomes
   class 0. `is_imputed` flags a value derived from a non-`is_observed` input or
   a USDM class carried past its weekly cadence. Duplicate release rows for one
   day are collapsed to the latest-available one before counting, so a
   re-ingested day can neither double-count nor disguise a short window as
   complete.

**Honest limitation, stated plainly.** In this warehouse *every* NASA
observation carries the same `data_available_at` (2026-07-26 00:06:17 UTC) and
every USDM polygon 2026-07-25, because the whole four-year history was ingested
in one governed pass. So for *this* dataset the `p_as_of_time` gate is real,
exercised and tested, but **not discriminating**: any as-of before 2026-07-26
returns nothing and any as-of after returns everything. The time-honesty of §4's
evaluation therefore rests on the *simulated* origin/cutoff split over
`observed_at`, which is exactly the same status the hindcast and iteration
planes already carry (`alembic/AGENTS.md`, `20260722_0006`): a simulated cutoff
is evaluation evidence, never an operational issue time.

## 4. D2 — the first model, and the honest scoreboard

`src/agri_data_service/execution/covariate_wind_model.py`. Dependencies: numpy
only (already a service dependency). No new packages.

**Method.** Direct multi-horizon ridge. For each horizon step h ∈ [0, 29], a
separate standardized closed-form ridge (α = 10.0, fixed a priori, never tuned
on the holdout) maps the 40-feature covariate vector at issue date D to the
WS2M value at D + h. Prediction intervals are the empirical p10/p90 of that
horizon's residuals on a held-out calibration window, floored at 0.

**Split (temporal only; no random split anywhere).**

| window | target dates | rows/horizon (min) |
| --- | --- | --- |
| fit | ≤ 2025-12-25 | 1195 |
| calibration (band only) | 2025-12-26 → 2026-06-23 | 180 |
| holdout | 2026-06-24 → 2026-07-23 | 30 |

**Baseline parity.** The single holdout origin is D = 2026-06-24, whose features
use data through 2026-06-23 — exactly the cutoff of the existing finalized
bootstrap iteration `boise-trekker-rim-ws2m-retrospective-20260623-30d`. Both
methods therefore forecast the identical 30 target dates from the identical
information cutoff. The baseline numbers are read through
`agri.forecast_iteration_evaluation`, not recomputed.

### 4.1 Head-to-head (30 target dates, 2026-06-24 → 2026-07-23)

| | MAE (m/s) | RMSE (m/s) | p10–p90 coverage | mean p10–p90 width (m/s) |
| --- | --- | --- | --- | --- |
| baseline `daily_increment_bootstrap_v1` | **0.5675** | **0.7132** | 0.9667 | 6.5799 |
| ridge over `agri_covariates_v1` | 0.6257 | 0.7659 | 0.8333 | 2.3212 |

**The model does not beat the baseline on point accuracy. It is worse on MAE and
worse on RMSE.** That is the headline and it does not change.

Coverage is **not** a third loss, and the first draft of this report was wrong to
bold 0.9667 as better. Both bands are nominal-80 % (p10–p90), so 0.9667
over-covers by 16.7 points while 0.8333 over-covers by 3.3, and the model's band
is 2.8x sharper (2.32 vs 6.58 m/s mean width, against a holdout whose actuals
have SD 0.652). A p10–p90 band 6.58 m/s wide on a 1.7 m/s mean is close to
uninformative.

These two interval results do **not** carry equal weight, and the difference
matters because this is the only comparison that favours the model. Mean band
width is a deterministic property of the emitted bounds, not a sampling
estimate, so **the 2.8x sharpness result is robust and does not depend on
n = 30**. The calibration comparison does: 0.8333 vs 0.9667 over 30 steps is
z = 1.77, so the two coverages are **not distinguishable at this sample size**
(model 95 % CI approximately [0.70, 0.97], baseline approximately [0.90, 1.00]),
and the same serial-correlation objection raised below applies here too. This
report therefore claims sharpness, not better calibration.

So on interval sharpness the model is ahead; on the point metrics that decide
whether this forecast is useful, it is behind.

Paired per-horizon-step comparison of absolute errors
(model − baseline): mean +0.0582 m/s, nominal SE 0.1312, t = 0.44, nominal 95 %
CI [−0.199, +0.315]. The model wins on 11 of 30 steps.

**That interval is mis-specified, not merely wide.** The 30 "observations" are 30
horizon steps from a *single* forecast origin: overlapping-target forecasts
issued from one information set, strongly serially correlated. The independence
assumption behind the paired t-statistic does not hold, so SE 0.1312 is
understated and the true interval is wider than quoted. A properly specified
test needs multiple independent origins, which the baseline plane does not yet
provide (it holds exactly one scored iteration). The conclusion is unchanged:
this comparison cannot distinguish the two methods, and nothing here licenses a
claim that the model is competitive.

Context for scale: the holdout actuals have mean 1.710 and SD 0.652 m/s; an
oracle that knew the holdout mean in advance would score MAE 0.546. Both methods
sit near that oracle, i.e. neither is extracting much beyond level.

### 4.2 Larger-sample out-of-fit point-only check

Fitting on data through 2025-12-25 and scoring every horizon step against every
target date in 2025-12-26 → 2026-06-23 gives 5400 out-of-fit point forecasts:
MAE 0.7310, RMSE 0.9002. Reported point-only: those dates are the calibration
window, so interval coverage there would be in-sample for the band and is
deliberately not quoted. The baseline plane has no comparable multi-origin
sample, so this is model-stability evidence, not a second head-to-head. The
model is fitted once per horizon on the fit window and is **not** refitted as the
target date advances, so this measures out-of-fit point skill rather than
re-estimation stability; the function is named `run_out_of_fit_point_scores`
accordingly.

### 4.3 Reproducibility

```
uv run python -m agri_data_service.execution.covariate_wind_model \
  --dsn postgresql://plantgeo_local_developer:***@127.0.0.1:5442/plantgeo_boise_completion_20260725 \
  --cell-id 7bec2286-23e7-4451-af51-5a589efeb2d8 \
  --series-id 4fc0ffd7-3cea-4dee-9346-bb27dd36e675 \
  --history-start 2022-07-23 --history-end 2026-07-23 \
  --origin-date 2026-06-24 --as-of-time 2026-08-02T00:00:00Z
```

`--as-of-time` is what makes this reproducible; it defaults to `now()`.
`p_as_of_time` is an input to `manifest_checksum`, so without pinning it two runs
over byte-identical governed data produce different checksums — the first draft
of this report omitted the flag, and its reproducibility claim was therefore
false. With it pinned, two consecutive runs were verified to produce identical
manifests and identical model output. Run as `plantgeo_local_developer` (which
inherits `EXECUTE` from standing default privileges, §5), not as the
`plantgeo_owner` superuser; the session issues `SET TRANSACTION READ ONLY`,
writes nothing, and rolls back. The ridge is closed-form with no seeds.

Verified checksums for the command above:
`manifest_checksum = 4a03a05b44da03018dd4ec5301545308648589078930b551a9d31a8789522af1`;
baseline `evaluation_checksum = 7d63cd557af654fa329cbf7d96ee361d04dd8d48ad49f9cdea269cb1d572b657`.

## 5. Governance posture

- **Evaluation-only.** None of the four functions references
  `v_forecast_series_serving`, `forecast_publication`,
  `forecast_publication_item` or `forecast_receipt`; a test asserts this against
  `pg_get_functiondef`. The trainer writes nothing and finalizes nothing.
- **Least privilege.** `REVOKE EXECUTE ... FROM PUBLIC` on all four; no grant to
  `plantgeo_forecast_reader`/`_writer`/`_publisher`/`_mv_refresher`.
  `plantgeo_local_developer` inherits `EXECUTE` from standing default
  privileges. A test asserts both halves.
- **Out of scope, untouched.** The strategy-selection label plane
  (`intervention_evidence_input`, `strategy_label_*`, `strategy_selection_*`)
  is still empty and stays empty; no labels were invented, no selection receipt
  finalized, and no `effect_candidate` path attempted (revision `0013` refuses
  it by design).
- **`drought_class_daily_series` body rewrite.** Revision `0016` also replaces
  that function's body (body-only, `or_replace`) to hoist the per-day
  `ST_Intersects` into one materialized admissible-polygon CTE. A four-year Boise
  window went from ~11 minutes (timed out at 2 min at 60 days ≈ 460 ms/day) to
  ~0.6 s. Without it the covariate layer is not usable as trainer input.

  For an **existing** cell the rewrite is row-identical: the day spine, the
  issue-date/severity/`geometry_checksum` tie-break order and both availability
  gates are preserved, and an independent two-way `EXCEPT ALL` differential over
  7 probes (checksum ties, non-intersecting polygons, window-edge issue dates,
  >7-day imputation, both gate boundaries) found 0 mismatches.

  It was **not** identical on its first pass, and this report previously claimed
  otherwise. Moving the `cell` CTE into the `admissible` subquery dropped the
  outer `CROSS JOIN cell`, so an unknown `p_cell_id` went from returning zero
  rows to returning one all-NULL `is_imputed = true` row per spine day — echoing
  the caller's own cell_id back as though it were a real cell whose drought was
  merely unresolvable, and propagating into `covariate_daily_features` as
  `drought_severity_imputed_lag_1 = 1.0` for the whole window. The outer
  `CROSS JOIN cell` is restored, and `covariate_daily_features` applies the same
  existence check to its own spine.

  The regression guard cited previously did not cover the rewritten paths: the
  0011 fixtures used one covering polygon for everything, never tied on
  `geometry_checksum`, and never probed an unknown cell. The 0011 contract test
  now adds all three cases, and the covariate contract test adds a
  duplicate-release case, so these are guarded rather than asserted.

## 6. Stated limitations

1. **9-day staleness.** History ends 2026-07-24; today is 2026-08-02. Known,
   accepted for this slice, no network backfill attempted. The holdout ends
   2026-07-23.
2. **ERA5-Land is a declared gap**, not a silent absence — see §2.2. No ERA5
   covariate exists in `agri_covariates_v1`.
3. **Single entity, single cell, single metric.** One parcel, one ~2244 km²
   NASA POWER analysis cell, one target signal. Nothing here generalises to
   another cell without re-fitting and re-evaluating, and the cell's spatial
   support is regional — it is not an acre-scale parcel observation.
4. **The `p_as_of_time` gate is not discriminating on this dataset** (§3).
5. **n = 30 for the head-to-head, and the paired test is mis-specified.** The 30
   horizon steps come from one origin and are serially correlated, so the quoted
   SE is understated (§4.1). Treat the comparison as unable to distinguish the
   two methods, not as evidence of parity.
6. **Intervals are empirical residual quantiles**, not calibrated confidence or
   life-safety bounds — the same caveat `20260723_0010` already records for the
   bootstrap's p10/p50/p90.
7. **No hyperparameter search.** α was fixed at 10.0 before any holdout was
   scored. Tuning it on the holdout would have been leakage; tuning it on the
   calibration window is available future work and is not claimed here.

## 7. Review history

An independent review of the first draft returned **changes-required**. It
reproduced the sweep, statistics and completeness figures exactly, confirmed the
governance, strict-lagging, evaluation-only and temporal-split claims, and
independently confirmed the drought rewrite is row-identical for an existing
cell. It also found two blockers and four majors. All are fixed here and all are
recorded in place rather than quietly corrected: the dropped `CROSS JOIN cell`
(§5), duplicate-release double counting (§2.3), a wall-clock-dependent
`manifest_checksum` (§4.3), a manifest lineage scan that attributed
non-contributing releases (§2.4), a regression guard that did not cover the
rewritten paths (§5), and the mis-bolded coverage comparison (§4.1).

After the dedupe fix the model was re-measured and the numbers are **unchanged**
(MAE 0.6256655632941667 before and after). The Boise history came from a single
ingestion pass, so it held no duplicate release rows for the dedupe to remove:
the defect was latent, not active, and is fixed on its merits rather than
because it moved a number.
