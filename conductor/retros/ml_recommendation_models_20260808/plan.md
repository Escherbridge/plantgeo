---
type: implementation-plan
---

# ML recommendation models plan

Sequenced by evidence-per-effort: each phase proves part of the loop on
existing plumbing before the next phase builds on it. Every phase ends with one
integrated sweep (tests, ruff, mypy, migration parity where touched) and an
independent review pass — no test→fix→test loops mid-phase.

> 2026-08-14 execution note: an earlier uncommitted tree faked parts of this
> track (hardcoded seed labels, heuristic "models", unpinned serving). That
> code was deleted and every item below was re-executed for real. Evidence
> files live in this track directory; the owner-signature request is
> `owner-signature-request-2026-08-14.md`.

## Phase 0 — audit and foundations

- [x] Audit what the `rag_recommendation_engine_20260324` track actually built:
  do `documents`/`knowledge_chunks`/species/companion tables exist in the
  migration head, and is pgvector active in the warehouse? Record findings in
  the track directory; do not assume the spec shipped.
  *Done 2026-08-14: `rag-track-audit-2026-08-14.md` — knowledge_chunks/species/
  companion_relationships exist with 0 rows, no `documents` table, pgvector
  0.8.5 active.*
- [x] Add `scikit-learn` (pinned `>=1.5,<2`) to `pyproject.toml`; regenerate
  `uv.lock`; confirm the Docker build path still resolves.
  *Done: pyproject pin + uv.lock resolve (sklearn 1.9.0). Container image build
  itself not exercised this session — resolution verified via uv.*
- [x] Inventory which forecast products exist per Boise-area series (MC
  iterations, ridge receipts, actuals maturity) to pick the AnEn pilot metric
  and the conformal calibration target from evidence.
  *Done: `forecast-product-inventory-2026-08-14.md`. Key finding: prod
  `drought_polygon_snapshot` is empty (USDM never ingested to prod), zeroing
  the covariate completeness mask for every cell; NDVI is the only live
  iteration family, so it is the conformal target.*

## Phase 1 — conformal self-correction (smallest change, proves the loop)

- [x] Implement split-conformal calibration over recorded
  `forecast_iteration_actual` residuals, availability-gated so no residual is
  used before its database-recorded arrival time.
  *Done: `method/ml/conformal_calibration.py` (true disjoint calibration/
  held-out folds, per-row recorded nominal bands) + `execution/
  conformal_recalibration.py` (read-only).*
- [x] Recalibrate the NDVI seasonal-anomaly bands on held-out origins; record
  before/after interval coverage against the nominal 80 % in a decision record.
  *Done on real prod residuals (276 calibration / 197 held-out, origin cutoff
  2026-05-01): coverage 67.0% → 89.8%. `conformal-recalibration-ndvi-2026-08-14.md`.*
- [x] Extract any non-trivial residual-read query into
  `src/agri_data_service/sql/execution/*.sql` per the standard.
  *Done: `sql/execution/select_forecast_iteration_residuals.sql`.*

## Phase 2 — AnEn k-NN forecast lane (the k-NN addition)

- [x] Implement `analog_ensemble_v1` as a database-free-at-the-core module in
  `execution/`, mirroring `covariate_wind_model.py`: neighbor search over the
  pinned covariate vector, per-feature distance weights, a temporal exclusion
  window around the query origin, empirical quantiles from analog successors,
  analog-space bias correction from availability-gated residuals.
  *Done: DB-free core in `method/ml/analog_ensemble.py` (layer contract forbids
  session code in `method/`), execution wiring in
  `execution/analog_ensemble_model.py` with a stricter whole-successor-path
  leakage boundary than the core's exclusion window.*
- [x] Persist through the wind-lane receipt chain (`forecast_training_run`,
  `forecast_backtest_metric`, method `ml`, canonical-JSON artifact holding
  hyperparameters + governed-data reference); `--persist` off by default.
  *Done: `execution/analog_ensemble_persist.py` reusing the wind lane's SQL
  unmodified; proven on the disposable warehouse with two durable, independently
  re-queried `validated` training runs (b0a76e03…, 184e5c13…). A real checksum-
  collision defect in the model document was found and fixed during the proof.*
- [x] Backtest one Boise metric at rolling origins with non-overlapping target
  spans against `daily_increment_bootstrap_v1` at identical origins; carry
  effective-sample-size caveats and feature-coverage shrinkage in
  `validation_metrics`. This doubles as the covariate-ablation answer: compare
  full-vector distance against target-lags-only distance.
  *Executed to the limit the data allows — `analog-ensemble-backtest-2026-08-14.md`:
  (1) `daily_increment_bootstrap_v1` does not exist in any database (the plan
  named a baseline that was never built); the framework backtest scores against
  pooled naive persistence instead. (2) A prod backtest is structurally refused
  (six Boise origins attempted, all `incomplete covariate vector`) because of
  the pre-existing USDM prod gap documented in Phase 0 — an ingest-lane job
  outside this track's read-only mandate. Rolling-origin backtest + both
  ablation variants + full receipt chain proven on the disposable warehouse
  with real MAE/RMSE/skill and disclosed fixture collinearity; the prod
  one-shot is documented in the same file.*

## Phase 3 — expert recommendation label plane

- [x] Author the additive schema: label source registry (work, edition,
  licence posture), label row (condition envelope, recommended
  species/practices, wildfire/water objective tags, citation locator,
  jurisdiction/climate context, review state), and release/checksum binding —
  one object per file in `db/agri/**`, forward-only Alembic migration,
  regenerate + parity green. No foreign key into the `0013` causal plane.
  *Done: `20260814_0022_expert_label_plane.py` (5 tables, 3 functions, 1
  trigger, 9 FKs, none into the 0013 plane); `db/agri/**` + manifest
  byte-identical to a head pg_dump.*
- [x] Run the label-harvest workflow (multi-agent, explicitly authorized):
  fan out extraction across Restoration Agriculture, NRCS CPS, SARE/extension
  publications, and Savanna Institute-class research sites; adversarially
  verify each extracted tuple against its cited source before it is staged.
  *Done 2026-08-14: 12-agent workflow (6 harvest slices + 6 adversarial
  citation verifiers, refute-by-default) — 32 candidates, 30 kept, 2 refuted;
  `label-harvest-2026-08-14.json`. Source mix deviation, recorded: the settled
  DOI-gate decision requires DOI-carrying sources, so extraction drew on
  peer-reviewed journals and DOI-bearing USDA/USFS reports covering the same
  practice families; most NRCS CPS / extension grey literature carries no DOI
  and was therefore not harvested.*
- [x] Load harvested labels as `draft` with full lineage; review and approve;
  reviewed labels only become trainable.
  *Done with the review gate honored, not bypassed: 28 loaded and advanced
  draft → `agent_reviewed` on citation verification (2 refuted → `rejected`,
  2 unloadable for missing envelopes, reported); `approved` is implemented but
  UNREACHED — it requires the owner's signature per the 2026-08-08
  evidence-chain decision. Training proceeds on `agent_reviewed` labels and
  every artifact carries `agent_reviewed_pending_owner_signature`. See
  `owner-signature-request-2026-08-14.md`.*
- [x] Map each approved condition envelope onto governed streams to emit
  feature-space training rows, recording every envelope term that our streams
  cannot yet express as an explicit gap.
  *Done over agent_reviewed labels: 1,299 training instances; three envelope
  terms (elevation_m, soil_texture, usda_hardiness_zone) are unexpressible
  (profile tables empty) and recorded as explicit per-instance gaps (847/539
  hits); aridity served as a declared Hargreaves/UNEP `derived_proxy`.*

## Phase 4 — `agri_covariates_v2`

- [x] Author the v2 feature schema as one reviewed change: MC-forecast
  statistics, ML/AnEn forecast statistics, seasonality terms, and
  per-issue-date as-of gating replacing the global cutoff (closes the
  documented revision-leakage gap).
  *Done: `20260814_0023_agri_covariates_v2.py` + `covariate_feature_schema`
  v2. Measured warehouse limit disclosed: bulk-backfilled releases carry only
  three `data_available_at` instants, so exact per-issue-date gating yields
  zero admissible rows; v2 prefers the issue-date-current revision and falls
  back only to the earliest-published one (never later — strictly less leaky
  than v1). v1 proven byte-identical over 29,240 rows.*
- [x] Rebuild feature snapshots under v2 for the pilot region; verify v1
  remains intact and queryable.
  *Done on the disposable warehouse; v1 unchanged (byte-identical proof above).*

## Phase 5 — Model A: species fit

- [x] Train the multi-label species-fit ranker on approved labels over v2
  features (k-NN and/or logistic per the JSON-artifact rule); wildfire
  prevention and water conservation enter as loss/scoring weights recorded in
  the receipt.
  *Trained for real (sklearn, actual fit on 401 design rows from 8 sources) on
  `agent_reviewed` labels — see Phase 3 note on the approval tier.*
- [x] Evaluate with spatially-blocked, temporally-honest splits; record
  per-source label-provenance breakdown so no single source dominates
  unnoticed.
  *Done with a recorded deviation: labels are too few/too single-source for
  meaningful spatial blocking, so evaluation used grouped leave-one-source-out
  CV with the per-source provenance breakdown. HONEST NEGATIVE RESULT:
  accuracy 0.0249 vs 0.5411 majority baseline — with one label per species per
  source, holding out a source removes all evidence for its species. The
  framework works; the label plane needs density before Model A is usable.
  `decision-record-2026-08-14.md`.*
- [x] Persist canonical-JSON artifact + training receipt; every returned
  species carries its supporting citations.
  *Done: artifact ed6216cc…, receipt 728e1de5… (disposable warehouse), DOI
  citations inline per returned item.*

## Phase 6 — Model B: strategy selection

- [x] Train the strategy-selection model over the named methods (reforestation,
  agroforestry, silvopasture, alley cropping, windbreaks, riparian buffers,
  managed grazing, cover cropping) on the same feature space and label plane.
  *Trained for real on 898 design rows from 14 sources. NEGATIVE-TO-NEUTRAL
  RESULT recorded: accuracy 0.5457 vs 0.5924 majority baseline, macro-F1
  0.2473 — evaluation-only candidate, not an operational recommender. The
  `strategy_selection` plane was NOT written to (still gated, still empty).*
- [x] Same evaluation, artifact, receipt, and citation discipline as Model A;
  outputs typed and phrased as literature-grounded recommendations.
  *Done: artifact 9ac45612…, receipt a679e1b6…, same discipline.*

## Phase 7 — serving and closeout

- [x] Expose both models through a bounded, release-pinned serving route with
  citations inline and the claim tier explicit; runtime queries in
  `sql/routes/*.sql` per the standard.
  *Done: `/api/v1/recommendations/{species,strategies}` — bounded SQL in
  `sql/routes/`, artifact checksum required in and echoed out, `as_of`
  required (a test forbids wall-clock reads), claim tier + review tier +
  `evaluation_only` inline, DOI citations per item, 409 `insufficient_labels`
  honesty path.*
- [x] One integrated sweep across the service (pytest, ruff, mypy, migration
  parity), then the independent quality review.
  *2026-08-14, in order: the independent quality review ran first and returned
  changes-required (receipt-writer fork, conformal empty-fold fabrication,
  AnEn method-layer leakage guard, expert-label INSERT gap — all four fixed
  with real-database evidence, plus every review minor). The integrated sweep
  then ran green: agri pytest 2943 passed / 3 skipped against the rebuilt
  head-migrated disposable warehouse (including byte-exact
  `test_declarative_schema_parity` and the readiness-revision check after
  bumping `EXPECTED_ALEMBIC_REVISION` to `20260814_0023`), `ruff check` +
  `ruff format --check` clean service-wide, `mypy src/` clean (174 files);
  root app: data-boundary, type-check, lint, and the full vitest suite all
  green in one chain.*
- [x] Decision record: what shipped, measured metrics per model, known gaps
  (label coverage, feature gaps, calibration residue), and the explicit
  statement that the `0013` causal plane remains blocked and untouched.
  *Done: `decision-record-2026-08-14.md` — includes the negative model
  results, the USDM prod gap, the pending owner signature, and the untouched,
  still-empty 0013 plane.*
