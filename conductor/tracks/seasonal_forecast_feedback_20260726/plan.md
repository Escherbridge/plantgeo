---
type: implementation-plan
---

# Seasonal forecast and residual-feedback plan

## Phase 0 — freeze evidence and decide whether to proceed

- [ ] Obtain an explicitly authorized read-only DSN or frozen, checksummed
  export of the retained warehouse. Do not use an owner/migration credential.
- [ ] Run the reviewed read-only forecast-iteration report and a data-quality
  profile for cadence, duplicates, nulls, source/release lineage, actual
  maturity, and seasonal coverage.
- [ ] Freeze the candidate source release and pre-register rolling origins,
  non-overlapping target windows (or a dependence-aware paired/block method),
  seasonal strata, metrics, quality thresholds, and untouched final origins.
- [ ] Stop with a durable abstention if multiple independent scored origins or
  final-holdout coverage are unavailable.

## Phase 1 — database-free benchmark

- [ ] Build a database-free, checksummed evaluation export binding original
  source availability, warehouse-recorded/as-of visibility, and only values
  available at each simulated origin.
- [ ] Evaluate persistence, seasonal-naive, SQL-linear, bootstrap, and
  regularized lag/seasonal candidates on identical expanding-time origins.
- [ ] Fit lags, imputation, scaling, season definitions, and hyperparameters
  only within training origins, then score the final holdout once. Record MAE,
  RMSE, bias, MAPE, skill, coverage, uncertainty, and per-season slices without
  changing any database row.
- [ ] Independently review the split, availability cutoffs, and residual
  calculations.

## Phase 2 — additive persistence design

- [ ] Write a migration specification for forecast signal definitions, immutable
  feedback values, candidate evaluations, and checksummed receipts.
- [ ] Prove parent references form a bounded acyclic graph, every parent cutoff
  and valid time precedes the child cutoff, and feedback availability is no
  earlier than every upstream actual-recorded time.
- [ ] Define exact integration points with existing feature snapshots and
  training runs; reject any path to publication or strategy efficacy.
- [ ] Implement only after the design and least-privilege review approve it.

## Phase 3 — evaluation-only implementation

- [ ] Add tests first for leakage rejection, cycle/depth rejection, checksum
  recomputation, immutable values, and no publication reachability.
- [ ] Implement the reviewed persistence plane and an evaluation-only candidate
  runner in a disposable database.
- [ ] Re-run the frozen benchmark and persist a new candidate rather than
  overwriting the rejected or historical candidate.
- [ ] Allow derived signals into an ML feature snapshot only after availability
  and lineage validation; keep model output unpublished.

## Phase 4 — decision record

- [ ] Record candidate-versus-baseline results, statistical uncertainty, failed
  gates, and an explicit accept/reject/abstain decision.
- [ ] Require an independent review and the project’s integrated test/lint/type/
  migration sweep.
- [ ] Keep Railway deployment, automatic scheduling, forecast publication, and
  `effect_candidate` finalization disabled unless separately authorized.

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
