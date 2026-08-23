---
type: track-spec
slug: ml_mojo_conversion_20260823
status: chartered
---

# ML → Mojo conversion

Chartered 2026-08-23. Owner: *"make a comprehensive ml conversion to mojo lane as well."*
This is the track the ML freeze has been waiting on, and it **gates**
[`fire_risk_zone_forecast_20260823`](../fire_risk_zone_forecast_20260823/spec.md).

## 1. Why this exists

The standing owner call — *"for now leave ml models alone, I'll engage separately on this — we are
swapping to mojo executables, but none of it matters until we have the right data sets and a solid
user experience"* — has been an open-ended freeze with no track behind it. Every new ML need has had
to ask whether it unfreezes. This track makes the migration explicit so that question has an address.

The data-set precondition is now substantially met: twelve Parquet streams exist, the gap-fill cron
is armed, and the warehouse is being populated. The freeze's own stated reason is expiring.

## 2. Scope — what actually moves

`method/ml/` holds **10 modules** today. The lattice already isolates them:
`method.monte_carlo` ↔ `method.ml` cross-imports are forbidden and enforced by
`tests/test_layer_import_contract.py`, which is exactly the boundary that makes extraction tractable
rather than a rewrite.

Inventory before planning. For each module decide: **port**, **retire**, or **stays Python**. Several
may not deserve a port at all — `plantgeo-strategy-selection` was measured as label-blocked with zero
trainable rows, and porting a model with no training data is pure waste.

## 3. The hard questions this track must answer

- **Where does Mojo actually run?** A separate service, or in-process? Railway has no Mojo buildpack
  today, so this is a Dockerfile and deploy-topology question before it is a language question.
- **What crosses the boundary?** Parquet is the obvious interchange — it is already the warehouse
  format and both runtimes read it. Avoid inventing an RPC surface.
- **How is equivalence proven?** A ported model must reproduce the Python model's output on a fixed
  seed and fixed input, or the port is a rewrite with a different answer. This needs a golden-output
  harness before the first port, not after.
- **What is the rollback?** If Mojo underdelivers, what stays runnable?

## 4. Explicit dependency

**`fire_risk_zone_forecast_20260823` is blocked on this track's runtime decision** — but only its
*model training*. That track's feature plane, label plane and evaluation harness are data work,
proceed now, and are portable to whichever runtime wins. **Do not let this track block that one's
data work.**

## 5. Related, and already decided

RUNBOOK §0.28.4 demoted Monte Carlo: it survives only where it can serve to train ML, as a labelled
climatology baseline any real forecast must beat. That makes the MC modules a **training input** to
this track's models rather than a parallel product — worth settling before porting anything, since
it changes what a ported model consumes.
