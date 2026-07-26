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
  privacy controls, and one initial target. The preferred first
  target is a water-use forecast/anomaly with 24 or more months of appropriately
  cadenced readings; otherwise stop with a data-coverage demo and abstention.
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
