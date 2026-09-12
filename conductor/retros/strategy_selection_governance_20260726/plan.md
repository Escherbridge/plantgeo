---
type: implementation-plan
---

# Strategy-selection governance plan

## Current-head execution correction — 2026-09-12

Migration `20260803_0018` retired the database finalizers and state guards that
the original step 3 assumed. Before any later normalization write, an
independently reviewed current-head normalization, validation, finalization and
export procedure must be approved and proven on a disposable database. The
[current source audit](outcome-label-source-audit-2026-09-12.md) records this
gate; it authorizes no database action.

1. Preserve the current abstention and label-audit evidence; do not invent or
   proxy intervention-effect labels.
2. Review the supplied source mapping against the minimum unblocker in the
   specification.
3. After the current-head procedure is independently approved, normalize
   approved rows into a disposable local database, finalize the label release,
   export the exact `strategy_labels_v1` bundle, and verify its receipt
   checksum.
4. Run the evaluation-only benchmark and retain all estimators, diagnostics,
   abstention/selection decision, and checksum evidence.
5. Independently review the result and run the integrated project checks before
   any separately authorized promotion discussion.

No step enables forecast publication, Railway mutation, `effect_candidate`
finalization, or a causal efficacy claim.
