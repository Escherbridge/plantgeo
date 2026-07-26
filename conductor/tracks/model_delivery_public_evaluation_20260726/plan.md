---
type: implementation-plan
---

# Model-delivery orchestrator plan

## Phase 0 — common evidence gate

- [ ] Rehash both frozen inputs and prove their receipt/manifest bindings.
- [ ] Record source/release/artifact/release-set lineage, native support, units,
  observed/valid/available/recorded clocks, code version, and local environment.
- [ ] Stop the individual lane if its evidence cannot be replayed exactly.

## Phase 1 — parallel read-only preparation

| Session | Lane | Deliverable | Fail-closed condition |
| --- | --- | --- | --- |
| A — crop data steward | Crop | Columnar export, data-quality profile, exclusions, and checksums | Non-finite bands, unresolved duplicates, or insufficient image-group class support |
| B — crop evaluation designer | Crop | Raw-to-feature mapping, deterministic `Image` split manifest, target/claim card | Any row-random split or undeclared shortcut feature |
| C — forecast evaluator | Forecast | Read-only cadence/availability profile and origin/season ledger | Mismatched frozen manifest, unavailable origin input, or unsupported metric calculation |
| D — forecast method designer | Forecast | Pre-registered persistence, seasonal-naive, and regularized lag/calendar recipe | Fold-external preprocessing/tuning or false independent-origin assumption |

## Phase 2 — integration gate

- [ ] Reconcile A–D into one evidence matrix with all input/export/split/recipe
  checksums.
- [ ] Approve crop execution only if every class has independent held-out-image
  support and no primary feature violates the mapping contract.
- [ ] Approve forecast execution only for the available seven-day backtest;
  pre-record 30-day seasonal selection as abstained unless additional independent
  origins and a final holdout are supplied.
- [ ] Independent reviewer returns approve, revise, or abstain before a writer
  changes a schema or runs final evaluations.

## Phase 3 — single-writer delivery

- [ ] Add forward-only typed benchmark facts/runs/predictions/evaluations only
  if the reviewed existing contract cannot represent the crop benchmark.
- [ ] Normalize GHISACONUS only into its source-specific fact plane; retain the
  source release and artifact bindings and do not copy unrelated warehouse data.
- [ ] Run majority, ridge/logistic, and one bounded nonlinear crop candidate.
  Choose using validation only; score the final image holdout once.
- [ ] Run the forecast candidates from the frozen export and emit a seven-day
  report or abstention. Do not mutate source releases or publish a forecast.
- [ ] Bind every delivery artifact to input, split, feature, recipe, model,
  prediction, metric, and environment checksums.

## Phase 4 — independent verification and decision

- [ ] Replay both artifact bindings and reproduce their final metric tables from
  a clean local environment.
- [ ] Verify grouped-fold integrity, no leakage, availability time, native
  support, calibration/abstention behavior, and demo-safe wording.
- [ ] Run the integrated project lint/type/test/schema sweep once after the
  complete implementation batch.
- [ ] Record one decision per lane: accepted evaluation artifact, revise, or
  abstain. Neither decision authorizes a production release or causal claim.

## Explicit stopping point

The track is complete when it contains a replayable historical crop-spectrum
artifact and a replayable seven-day forecast backtest or abstention artifact,
both independently reviewed. Advancement of seasonal forecasting requires new,
independent 30-day origins and a pre-registered final holdout. Advancement of
any other goal requires a separately retained target contract.
