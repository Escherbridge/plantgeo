# Boise Trekker Rim data-completion slice — pipeline evidence report

Date: 2026-07-25/26 (UTC boundary crossed mid-run). Status: **complete for the
first slice's definition of done.** All quantities below are **pipeline
evidence** — they prove the governed data-completion and evaluation framework
runs end-to-end on fresh, spatially-relevant, issue-window-current data. They
are **not** an operational, calibrated, or life-safety-validated weather or
drought forecast, and must not be represented as one.

## 1. Target and completeness definition

| | |
|---|---|
| Entity | Trekker Rim parcel, Boise, ID — Ada County parcel R0541500060, City of Boise Parks & Recreation, 5.232 acres, centroid 43.55595 N, −116.131577 E |
| Primary metric | `wind_speed` (NASA POWER `WS2M`, daily, m/s), spatial support: one 0.5° NASA POWER point-sample cell `boise-local:trekker-rim:p43.556:m116.132` (55.66 km support — regional accounting only, not parcel-native pixels) |
| Secondary context | USDM weekly D0–D4 drought polygons intersecting the cell |
| Complete means | four-calendar-year history (validator-enforced window 2022-07-23 → 2026-07-23) pinned in validated release sets through the latest fully-observed UTC day; per-signal coverage audits recorded; provenance = per-release payload checksums + license snapshots + release-set manifest checksums; a finalized, time-honest forecast iteration with reconciled actuals and a fresh evaluation |

Contract of record: `services/agri-data-service/plans/boise-trekker-rim-completion-contract-2026-07-25.md`.

## 2. What was pinned (disposable warehouse `plantgeo_boise_completion_20260725`, Alembic `20260725_0011`)

**NASA POWER** (plan `nasa-power-boise-trekker-rim-20220723-20260723.json`):
1,462 days × 7 parameters = 10,234 observations, **zero missing wind days**;
all 7 signal coverage audits `complete`. Release set
`nasa-power-boise-trekker-rim-20220723-20260723-asof-20260726`
(the plan's draft `…-acquisition` key was superseded at finalization by this
as-of-stamped key when the wall clock crossed UTC midnight;
id `9781c4c6-a79f-414f-8b61-85f5874675b1`, manifest
`d4258792cea4864c30ce3aab7263a3082bdf58b00570e9e6c1394bc5e2032c0d`), validated
2026-07-26T00:08:45Z. `ALLSKY_SFC_SW_DWN` was deliberately excluded: the
satellite-derived radiation product lags ~3 months (empirically complete only
through 2026-04-30 — the same lag that stranded the superseded Denver
fixture), and the plan's all-or-nothing coverage gate would otherwise reject
the whole window.

**USDM** (plan `usdm-boise-20220724-20260724.json`): 209 weekly issues
(2022-07-26 → 2026-07-21), 1,045 drought polygons, all 209 source coverage
audits `complete`. Release set `usdm-boise-20220724-20260724-acquisition`
(id `7c159a93-0784-4397-b78c-e2d3276a7ee0`, manifest
`727e1d3010dfaf70063b2799138783721cf35b4c9beab705720bd00b7dbd7393`).

**Forecast series** `nasa-power-ws2m-boise-trekker-rim-v1`
(id `4fc0ffd7-3cea-4dee-9346-bb27dd36e675`), spatial cell
`7bec2286-23e7-4451-af51-5a589efeb2d8`, metadata pinned to the NASA release
set and parcel identity. This series supersedes the stale Denver-point
evidence (`na-sample:1deg:p040.00:m105.00`, history ending 2026-04-30) as the
current evaluation candidate; the Denver planes remain untouched (append-only).

## 3. Iterations and time-honest evaluation

Both iterations: method `daily_increment_bootstrap_v1`, 30-day horizon, 1,000
simulations, seed 42, `gap_policy=strict`, `lower_bound=0`, purpose
`evaluation_only` (schema-enforced; structurally excluded from every
publication/serving surface).

| iteration | cutoff | evaluated | actuals | MAE (m/s) | RMSE (m/s) | p10–p90 coverage |
|---|---|---|---|---|---|---|
| `boise-trekker-rim-ws2m-retrospective-20260623-30d` | 2026-06-23 | 30 | 30/30 | **0.5675** | **0.7132** | **0.9667** |
| `boise-trekker-rim-ws2m-current-20260723` | 2026-07-23 | 30 | 0 (none observable yet) | — | — | — |

Numbers produced by the new `agri.forecast_iteration_evaluation(...)`
function (migration 0011) reading `agri.v_forecast_iteration_outcome`; every
actual is gated on server-recorded `actual_recorded_at`, and the current
iteration correctly reports NULL metrics rather than fabricated zeros.
Receipt checksums: retrospective `5f29be0d…4720f59`, current
`0c734a69…3e35c059`. For scale only: the superseded stale Denver baseline
reported MAE 0.79 / RMSE 0.898 / coverage 0.967 on its own window; the
figures are not directly comparable (different cell, window, season) and
neither is an operational skill claim. The v1 bootstrap assumes exchangeable
daily increments (no seasonality/autocorrelation); the retrospective median
visibly under-forecast a late-June wind uptick.

Drought context via the new `agri.drought_class_daily_series(...)`: the cell
sits in **D1** as of the 2026-07-21 issue (leakage-gated on
`data_available_at`, weekly hold-over non-imputed, `is_imputed` flags any
carry beyond 7 days).

## 4. Verification state

`ruff format --check`, `ruff check`, `mypy src` clean. Full pytest sweep:
**212 passed, 6 skipped** (credential/env-gated suites), including
byte-for-byte declarative-schema parity, the new signal-plane contract tests,
and the 0010 iteration contract test on a 0011 database. The independent
test lane caught one real spec deviation before it shipped: the evaluation
checksum originally ingested a `numeric`-rendered coverage value instead of
the returned `double precision` — fixed in the in-flight 0011 SQL so the
checksum binds exactly the values the function returns.

## 5. Recorded deviations and open items

1. **Loader guard widened (reviewed change):** `LOCAL_SOURCE_LOADER_DATABASE_URL`
   now accepts `plantgeo_`-prefixed disposable databases, mirroring the
   iteration-guard precedent; covered by new config tests.
2. **Least-privilege gap on release-set finalize:** the 0009
   `protect_intervention_evidence_parents` trigger reads two intervention
   tables on *any* release-set finalize, and `plantgeo_loader` lacks those
   SELECTs on this disposable DB. Granting them (the already-reviewed
   `grant-resolution-aware-loader.sql`) was **denied by the session permission
   gate at every level** and was **not** applied. Finalize transactions ran as
   `plantgeo_owner` without the role switch — data-identical, but the
   least-privilege demonstration for finalize is **not** made here. Follow-up
   for review: widen that script's database-name gate (it currently admits
   only `plantgeo` and `plantgeo_geospatial_test_*`) and re-run the
   demonstration in a fresh disposable DB.
3. Iteration CALLs ran as `plantgeo_owner` (the `plantgeo_local_developer`
   credential was not available to the session) — evaluation-only semantics
   are schema-enforced regardless of caller.
4. The current iteration records `availability_mode='retrospective_pinned_release'`
   because its as-of necessarily follows cutoff+1 day — a property of the
   0010 boundary rule, not an error.
5. **ERA5-Land remains an open gap** (CDS credentials unavailable), as do all
   framework blockers from the North America pilot report (no legal parcel
   authority, uneven open coverage, PG18 rehearsal before any Railway move).
