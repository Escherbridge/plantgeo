---
type: implementation-plan
---

# Restoration Agriculture demo plan

## A–C: parallel read-only discovery

| Session | Deliverable | Gate |
| --- | --- | --- |
| A — data steward | Source register, source-of-source provenance, terms-version/derivative-use review, and data-quality acceptance checklist | No bulk capture or Kaggle use before approval; utility data needs documented authorization/consent |
| B — data architect | Additive mapping, lineage/availability DAG, privacy boundary, and first vertical-slice contract | No migration or database write before design review |
| C — evaluation/product | Separate targets, baselines, folds, metrics, abstention rules, and safe goal-selector UX | No strategy-efficacy wording |

## D: integration review

- Reconcile A–C into a single source-to-signal mapping.
- Select one authorized demo AOI with boundary provenance and purpose-limited
  privacy controls, then map the shared evidence plane to separately governed
  goal models: water, energy, vegetation, soil, yield/cost, biodiversity, and
  scenario exploration. A goal is eligible only when its historical records
  meet its pre-registered cadence, coverage, and availability requirements;
  otherwise the UI must show the evidence gap and abstain.
- Pre-register source releases, data availability, folds, seasonal coverage,
  final holdout, quality thresholds, and user-facing limitations.

## E: single-writer implementation

- Add only reviewed, additive persistence and evaluation code.
- Freeze exact releases and exports; profile grain, nulls, duplicates, units,
  time zones, corrections, meter/zone cardinality, scale, and leakage before
  fitting a model.
- Benchmark persistence, seasonal-naive, and time-honest regularized baselines
  before accepting any more complex candidate.

## F: independent verification and demo decision

- Verify source terms, checksums, availability cutoffs, split integrity,
  uncertainty, UI wording, and all abstention paths.
- Run the integrated project checks once after the implementation batch.
- Record an explicit demo/abstain decision. A demo never upgrades into a
  production release or causal recommendation without separate authorization.

## Local-validation stopping point and Railway handoff

Stop this track at the first point all of the following are true:

- The isolated local stack starts from documented configuration, applies a
  fresh schema successfully, and passes browser QA for the goal-selector and
  abstention states.
- Every candidate source has a versioned terms/provenance/checksum record;
  utility data additionally has documented authorization and an approved AOI
  boundary. Unsupported sources remain out of the import plane.
- Each eligible goal has time-honest train/validation/final-holdout evidence,
  quality and seasonal-coverage gates, a baseline comparison, and a durable
  abstention result when those gates fail.
- Strategy selection runs only when real intervention/control outcome labels
  can be bound into the governed label plane. Forecast residuals, utility
  readings, and remote-sensing values alone are predictive targets, not
  treatment-effect labels.
- A local handoff manifest identifies the exact image revision, migrations,
  source releases, checksums, artifacts, required environment values, and
  browser/stack evidence. It contains no production data movement.

Only after this stop point may a separately authorized Railway release review
decide whether to deploy the approved artifacts. Deployment, publication,
materialization, and causal-effect finalization are deliberately separate
operations.
