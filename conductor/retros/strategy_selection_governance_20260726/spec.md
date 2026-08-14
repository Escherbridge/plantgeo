---
type: specification
---

# Strategy-selection governance

## State

The research/evaluation-only strategy-selection plane is implemented through
schema revision `20260725_0013`, including governed outcome definitions,
intervention/control labels, policies, exact label export, training lineage,
selection receipts, and checksum binding. `effect_candidate` finalization is
deliberately disabled.

The track is **blocked**: the audit found no real governed intervention/control
outcome labels in the authorized working tree or local data sources. Boise
forecast actuals and residuals are forecast-error labels, not treatment-effect
outcomes, and must never be substituted.

## Minimum unblocker

Provide a reviewed source mapping that identifies the outcome definition,
treatment strategies, eligible controls, assignment/cohort times, spatial
blocks, baseline/outcome windows, covariates with availability times, and exact
source/release lineage. The mapping must be normalizable into the checksum-bound
label plane on a disposable local database.

## Invariants

- Outputs remain research/evaluation-only and cannot be published or called
  causal recommendations.
- A label release checksum binds database export, strict bundle, model artifact,
  training receipt, and selection digest.
- Forecast residuals may enter an availability-aware ML feature snapshot only
  under the seasonal-feedback contract; they are never strategy labels.
- Any future benchmark must retain the existing abstention gates for support,
  overlap, balance, estimator agreement, and paired best-versus-second contrast.

See [`docs/reports/strategy-selection-label-audit-2026-07-25.md`](../../../docs/reports/strategy-selection-label-audit-2026-07-25.md)
for the negative source-search evidence.
