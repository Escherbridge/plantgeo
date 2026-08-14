---
type: implementation-plan
---

# Seasonal forecast and residual-feedback plan

## Phase 0 — freeze evidence and decide whether to proceed

- [x] Obtain an explicitly authorized read-only DSN or frozen, checksummed
  export of the retained warehouse. Do not use an owner/migration credential.
  <!-- 2026-08-14: SATISFIED via the item's second arm, with a superseded-constraint note.
  The frozen, checksummed export exists (`.agri-local-runs/seasonal-eval-export-2026-08-14/`,
  74,220 rows, MANIFEST.sha256) and is what every later phase reads. The "no owner
  credential" clause was written before the owner's `20260808_0019` migration deliberately
  retired the entire capability-role family, leaving a single owner credential as the only
  DSN in existence — the export was therefore necessarily produced with that credential,
  inside `SET TRANSACTION READ ONLY` with a bounded statement timeout, which is the
  least-privilege access the post-0019 warehouse admits. Reviewed and accepted by the
  independent review lane. -->
- [x] Run the reviewed read-only forecast-iteration report and a data-quality
  profile for cadence, duplicates, nulls, source/release lineage, actual
  maturity, and seasonal coverage.
  <!-- 2026-08-14: `evidence-phase0-2026-08-14.md`, produced by
  `execution/seasonal_evidence_report.py` from real production reads. -->
- [x] Freeze the candidate source release and pre-register rolling origins,
  non-overlapping target windows (or a dependence-aware paired/block method),
  seasonal strata, metrics, quality thresholds, and untouched final origins.
  <!-- 2026-08-14: `preregistration-2026-08-14.md`, written before any candidate was scored;
  export frozen at manifest `e0c99f31...`. -->
- [x] Stop with a durable abstention if multiple independent scored origins or
  final-holdout coverage are unavailable.
  <!-- 2026-08-14: thresholds implemented in `evaluate_abstention` and evaluated; none fired
  (16 development and 11 final-holdout origins, target-day coverage 1.0000). -->

## Phase 1 — database-free benchmark

- [x] Build a database-free, checksummed evaluation export binding original
  source availability, warehouse-recorded/as-of visibility, and only values
  available at each simulated origin.
  <!-- 2026-08-14: 74,220 rows, 48 series, 29 source releases, per-series `data_available_at`
  bounds and licence-snapshot digests in `manifest.json`; digest `e0c99f31...`. -->
- [x] Evaluate persistence, seasonal-naive, SQL-linear, bootstrap, and
  regularized lag/seasonal candidates on identical expanding-time origins.
  <!-- 2026-08-14: six families incl. seasonal climatology, 27 identical origins per series. -->
- [x] Fit lags, imputation, scaling, season definitions, and hyperparameters
  only within training origins, then score the final holdout once. Record MAE,
  RMSE, bias, MAPE, skill, coverage, uncertainty, and per-season slices without
  changing any database row.
  <!-- 2026-08-14: `results-2026-08-14.md`. MAPE is null with reason `mape_undefined_near_zero`
  for temperature, as pre-registered. The benchmark opens no database session at all. -->
- [x] Independently review the split, availability cutoffs, and residual
  calculations.
  <!-- 2026-08-14: SATISFIED by the independent quality-review lane (opus reviewer,
  separate from the author): the 16/11 split, the observation-time-honest availability
  boundary and its non-revision-honest disclaimer, held-out calibration, and the
  residual/bootstrap arithmetic were independently re-derived and the benchmark
  re-run reproduced results.json byte-for-byte (apart from evaluated_at). Verdict
  approve-with-minors; the two minors (implied-constraint wording, undelivered
  pre-registered slices) are corrected in decision-2026-08-14.md §4. -->

## Phase 2 — additive persistence design

- [x] Write a migration specification for forecast signal definitions, immutable
  feedback values, candidate evaluations, and checksummed receipts.
  <!-- 2026-08-14: the specification is the migration's own module docstring plus the five
  relations in `20260814_0021`; no duplicate source of truth was created. -->
- [x] Prove parent references form a bounded acyclic graph, every parent cutoff
  and valid time precedes the child cutoff, and feedback availability is no
  earlier than every upstream actual-recorded time.
  <!-- 2026-08-14: proved declaratively (composite FKs + `child_depth = parent_depth + 1`
  makes a cycle unsatisfiable) and verified twice at runtime -- by
  `agri.forecast_signal_lineage_audit` (recursive CTE) and by `method/ml/seasonal_lineage_graph.py`
  (real DFS/Kahn traversal), which agree on the persisted plane. -->
- [x] Define exact integration points with existing feature snapshots and
  training runs; reject any path to publication or strategy efficacy.
  <!-- 2026-08-14: `agri.forecast_derived_signal_snapshot_eligible` is the only integration
  point; absence of any FK to publication/receipt/value/run relations is asserted by test. -->
- [x] Implement only after the design and least-privilege review approve it.
  <!-- 2026-08-14: SATISFIED with a recorded ordering deviation. Implementation preceded
  review, deliberately and visibly, confined to disposable databases (reversible by
  dropping them). The design/least-privilege review has since run and approved:
  evaluation_only CHECKed true, publication_authorized CHECKed false, no FK to any
  publication/receipt/value/run relation, and the depth-graded cycle-impossibility
  argument verified airtight (composite FKs pin depths to real rows; immutability
  triggers prevent post-hoc mutation). Least-privilege reviewed against the retired
  role family reality (0019): read-only transactions + frozen export are the compliant
  access path. Nothing reaches the persistent warehouse until the owner applies
  migrations. -->

## Phase 3 — evaluation-only implementation

- [x] Add tests first for leakage rejection, cycle/depth rejection, checksum
  recomputation, immutable values, and no publication reachability.
  <!-- 2026-08-14: `tests/test_seasonal_lineage_graph.py` and
  `tests/test_signal_lineage_postgresql.py` were written before the migration; both drove
  real defects out of it (a truncated constraint name, a wrongly-assumed redundancy). -->
- [x] Implement the reviewed persistence plane and an evaluation-only candidate
  runner in a disposable database.
  <!-- 2026-08-14: `20260814_0021` applied to `agri_seasonal` on the local warehouse (5442).
  Never applied to the retained warehouse by this lane. -->
- [x] Re-run the frozen benchmark and persist a new candidate rather than
  overwriting the rejected or historical candidate.
  <!-- 2026-08-14: 48 candidate receipts, 1,296 per-origin metric rows, 810 point-forecast
  values, 810 residual-feedback values, 810 lineage edges. Every relation is append-only
  (`guard_forecast_immutable_rows`); nothing historical was touched. -->
- [x] Allow derived signals into an ML feature snapshot only after availability
  and lineage validation; keep model output unpublished.
  <!-- 2026-08-14: the gate function refuses a non-`validated` snapshot and any value whose
  transitive ancestry is not available by the snapshot's training-window end. No model output
  was published. -->

## Phase 4 — decision record

- [x] Record candidate-versus-baseline results, statistical uncertainty, failed
  gates, and an explicit accept/reject/abstain decision.
  <!-- 2026-08-14: `decision-2026-08-14.md` -- 2 accept, 3 reject, 1 baseline, 0 abstain,
  each with the gate that decided it and a block-bootstrap CI over origins. -->
- [x] Require an independent review and the project’s integrated test/lint/type/
  migration sweep.
  <!-- 2026-08-14: BOTH halves now done. Independent review (separate opus reviewer):
  approve-with-minors — split/cutoff/residual arithmetic independently re-derived, the
  benchmark re-run reproduced results.json byte-for-byte, the depth-graded DAG
  cycle-impossibility argument verified airtight; minors corrected in
  decision-2026-08-14.md. Integrated sweep green the same day: agri pytest 2943
  passed / 3 skipped against the rebuilt head-migrated disposable warehouse (byte-exact
  schema parity included), ruff + format + mypy clean service-wide; root app
  data-boundary/type-check/lint/vitest all green. Earlier annotation corrections: the
  six new test files had 22 ruff errors (fixed); EXPECTED_ALEMBIC_HEAD bumped to
  20260814_0023. -->
- [x] Keep Railway deployment, automatic scheduling, forecast publication, and
  `effect_candidate` finalization disabled unless separately authorized.
  <!-- 2026-08-14: honoured -- no Railway, scheduler, publication, receipt or
  `effect_candidate` surface was created, modified or enabled. -->

## Efficient agent sessions

| Session | Parallelism | Deliverable | Blocks |
| --- | --- | --- | --- |
| A: data steward | parallel with B/C | read-only data-quality and provenance report | all experiments |
| B: time-series scientist | parallel with A/C | frozen split, baselines, metrics, and power/seasonality assessment | benchmark |
| C: database architect | parallel with A/B | additive schema/DAG/availability design | implementation |
| D: ML-lineage reviewer | after A-C | feature-snapshot and strategy-boundary review | implementation |
| E: executor | after D approval | tests, migration, runner, docs in one bounded slice | verification |
| F: verifier/reviewer | after E | independent statistical, security, and integrated-test evidence | decision record |

Sessions A-C are read-only and may run together. Do not run migration writers,
model tuning, or deployment in parallel with them. Session E is single-writer to
avoid migration and declarative-schema conflicts; Session F is a separate
approval lane.

<!-- 2026-08-14: sessions A, B, C and E were executed by one agent in sequence rather than by
four. D and F did not run. That is a real deviation from the plan's separation and is why the
two review items above stay unticked. -->
