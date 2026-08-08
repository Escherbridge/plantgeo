---
type: implementation-plan
---

# ML recommendation models plan

Sequenced by evidence-per-effort: each phase proves part of the loop on
existing plumbing before the next phase builds on it. Every phase ends with one
integrated sweep (tests, ruff, mypy, migration parity where touched) and an
independent review pass — no test→fix→test loops mid-phase.

## Phase 0 — audit and foundations

- [ ] Audit what the `rag_recommendation_engine_20260324` track actually built:
  do `documents`/`knowledge_chunks`/species/companion tables exist in the
  migration head, and is pgvector active in the warehouse? Record findings in
  the track directory; do not assume the spec shipped.
- [ ] Add `scikit-learn` (pinned `>=1.5,<2`) to `pyproject.toml`; regenerate
  `uv.lock`; confirm the Docker build path still resolves.
- [ ] Inventory which forecast products exist per Boise-area series (MC
  iterations, ridge receipts, actuals maturity) to pick the AnEn pilot metric
  and the conformal calibration target from evidence.

## Phase 1 — conformal self-correction (smallest change, proves the loop)

- [ ] Implement split-conformal calibration over recorded
  `forecast_iteration_actual` residuals, availability-gated so no residual is
  used before its database-recorded arrival time.
- [ ] Recalibrate the NDVI seasonal-anomaly bands on held-out origins; record
  before/after interval coverage against the nominal 80 % in a decision record.
- [ ] Extract any non-trivial residual-read query into
  `src/agri_data_service/sql/execution/*.sql` per the standard.

## Phase 2 — AnEn k-NN forecast lane (the k-NN addition)

- [ ] Implement `analog_ensemble_v1` as a database-free-at-the-core module in
  `execution/`, mirroring `covariate_wind_model.py`: neighbor search over the
  pinned covariate vector, per-feature distance weights, a temporal exclusion
  window around the query origin, empirical quantiles from analog successors,
  analog-space bias correction from availability-gated residuals.
- [ ] Persist through the wind-lane receipt chain (`forecast_training_run`,
  `forecast_backtest_metric`, method `ml`, canonical-JSON artifact holding
  hyperparameters + governed-data reference); `--persist` off by default.
- [ ] Backtest one Boise metric at rolling origins with non-overlapping target
  spans against `daily_increment_bootstrap_v1` at identical origins; carry
  effective-sample-size caveats and feature-coverage shrinkage in
  `validation_metrics`. This doubles as the covariate-ablation answer: compare
  full-vector distance against target-lags-only distance.

## Phase 3 — expert recommendation label plane

- [ ] Author the additive schema: label source registry (work, edition,
  licence posture), label row (condition envelope, recommended
  species/practices, wildfire/water objective tags, citation locator,
  jurisdiction/climate context, review state), and release/checksum binding —
  one object per file in `db/agri/**`, forward-only Alembic migration,
  regenerate + parity green. No foreign key into the `0013` causal plane.
- [ ] Run the label-harvest workflow (multi-agent, explicitly authorized):
  fan out extraction across Restoration Agriculture, NRCS CPS, SARE/extension
  publications, and Savanna Institute-class research sites; adversarially
  verify each extracted tuple against its cited source before it is staged.
- [ ] Load harvested labels as `draft` with full lineage; review and approve;
  reviewed labels only become trainable.
- [ ] Map each approved condition envelope onto governed streams to emit
  feature-space training rows, recording every envelope term that our streams
  cannot yet express as an explicit gap.

## Phase 4 — `agri_covariates_v2`

- [ ] Author the v2 feature schema as one reviewed change: MC-forecast
  statistics, ML/AnEn forecast statistics, seasonality terms, and
  per-issue-date as-of gating replacing the global cutoff (closes the
  documented revision-leakage gap).
- [ ] Rebuild feature snapshots under v2 for the pilot region; verify v1
  remains intact and queryable.

## Phase 5 — Model A: species fit

- [ ] Train the multi-label species-fit ranker on approved labels over v2
  features (k-NN and/or logistic per the JSON-artifact rule); wildfire
  prevention and water conservation enter as loss/scoring weights recorded in
  the receipt.
- [ ] Evaluate with spatially-blocked, temporally-honest splits; record
  per-source label-provenance breakdown so no single source dominates
  unnoticed.
- [ ] Persist canonical-JSON artifact + training receipt; every returned
  species carries its supporting citations.

## Phase 6 — Model B: strategy selection

- [ ] Train the strategy-selection model over the named methods (reforestation,
  agroforestry, silvopasture, alley cropping, windbreaks, riparian buffers,
  managed grazing, cover cropping) on the same feature space and label plane.
- [ ] Same evaluation, artifact, receipt, and citation discipline as Model A;
  outputs typed and phrased as literature-grounded recommendations.

## Phase 7 — serving and closeout

- [ ] Expose both models through a bounded, release-pinned serving route with
  citations inline and the claim tier explicit; runtime queries in
  `sql/routes/*.sql` per the standard.
- [ ] One integrated sweep across the service (pytest, ruff, mypy, migration
  parity), then the independent quality review.
- [ ] Decision record: what shipped, measured metrics per model, known gaps
  (label coverage, feature gaps, calibration residue), and the explicit
  statement that the `0013` causal plane remains blocked and untouched.
