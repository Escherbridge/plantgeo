---
type: track-spec
slug: repository_conformity_hardening_20260901
status: planned
---

# Repository conformity, reuse, and dead-code hardening

## Purpose

Make PlantGeo's checked-in engineering standards executable, remove confirmed dead weight, and
restore canonical ownership where runtime and operator code have forked it. This is an evidence-led
refactor track: a static search creates a candidate, never deletion authority.

The 2026-09-01 audit found one immediate safety defect: the moderation UI displays a fabricated
causal benefit estimate and confidence interval beside approve/publish and activation controls.
That scorecard is removed first and is not replaced by another placeholder.

## Scope

- remove fabricated or unproven decision-support claims from consequential controls;
- enforce unused-symbol, Python script typing, architecture-boundary and pre-deploy quality gates;
- move reusable execution/workflow logic out of the Click adapter;
- consolidate governed snapshot-builder receipt, verification and finalization machinery behind
  typed product specifications with byte-for-byte equivalence fixtures;
- split multi-responsibility Parquet modules without changing their public contracts;
- remove confirmed source/dependency orphans and quarantine contingent candidates behind proof;
- reconcile topology documentation, deprecated shims, TODO ownership and dormant migrations.

## Out of scope

- fixing active production Parquet data, reader or polygon incidents inside this track;
- disabling a Railway writer, activating executor lanes or deleting PostgreSQL/R2 data;
- removing migration/history evidence because it has no runtime import;
- bulk export-style or file-name churn unrelated to a touched module.

## Proof-before-delete contract

1. Prove zero static, dynamic, route, command, test-fixture and external operator consumers.
2. Name the canonical replacement and prove contract parity when the candidate is superseded.
3. For packages, regenerate the locked graph and run import, image-build and bundle smoke checks.
4. For routes/services, add production request/deployment evidence and a rollback before removal.
5. For migrations/history, preserve a typed state, reason and production fingerprint; absence from
   an import graph is irrelevant.
6. Apply accepted fixes before one final formatter/linter/type/test/build sweep and independent
   review. Do not certify each deletion with a separate partial green run.

## Acceptance gates

1. No fabricated effect estimate or confidence interval appears beside an intervention decision.
2. Normal TypeScript/Python gates detect the unused-symbol and script-typing classes found here.
3. `interface/cli` contains adapters only; reusable lane/framework/orchestration logic has a domain
   owner and CLI help/exit-code snapshots are unchanged.
4. Consolidated snapshot builders reproduce existing bytes, SHA-256 receipts, checkpoints,
   manifests and `_COMPLETE` records for golden fixtures.
5. Every removed module/dependency has a stored proof packet; every retained candidate has a named
   blocker rather than an indefinite “maybe unused” status.
6. Production topology docs resolve to one dated, machine-verified service/database inventory.
7. Final frontend and Python sweeps, image/build smoke, and independent review are green on the exact
   integrated tree.
