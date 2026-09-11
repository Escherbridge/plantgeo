---
type: track-plan
slug: repository_conformity_hardening_20260901
status: active
resource: ./spec.md
---

# Plan

## Current checkpoint — September 11

`9b239fa` retired obsolete snapshot dispatch and `3632d61` retired unused MTBS
readers; the [cleanup proof](../../retros/parquet_cutover_completed_slices_20260910/cleanup-proof.md)
and [verification record](../../retros/parquet_cutover_completed_slices_20260910/verification.md)
preserve those completed removals. The September 8–9 baseline/rebuild also
supersedes old dormant-migration filenames as live deployment instructions.
The September 11 bounded closure inventory classifies every remaining candidate and records
the current owner before any edit. Six forecast workflows and all six shared chunk-framework
types now have execution-layer owners. The thin-CLI strict xfail remains only for 18 exact
transaction sites held by active executor/source-product owners; an exact-site passing guard
prevents either new debt or a sideways move while those handoffs are pending.

## Wave C0 — safety and evidence freeze

- [x] Remove the fabricated moderation scorecard; show unavailable evidence honestly
  (2026-09-02, `2b4cfef`; the fabricated `causalTauEst ?? 0.15` submission
  default was subsequently removed in `ad4e015`, with absent estimates omitted).
- [x] Store the audit inventory with each candidate classified as immediate, confirmed, contingent,
  refactor, enforcement gap, or protected evidence.
- [x] Freeze file ownership with the active Parquet tracks before shared edits; reader,
  offline-builder, generic availability/scheduler, migration, and product transaction surfaces
  were recorded as handoffs and left untouched (2026-09-11).

## Wave C1 — executable standards

- [x] Enable an intentional TypeScript unused-symbol policy and clear its production findings (2026-09-02, `12fa189`).
- [x] Extend Python typing/architecture checks to operator scripts and thin adapters (2026-09-02; the thin-adapter rule is pinned as a strict xfail until c2).
- [x] Add a locked Python quality receipt to the production build/deploy path (2026-09-02: `QUALITY_RECEIPT.json` + `scripts/verify_quality_receipt.py` in both runtime Dockerfiles).

## Wave C2 — canonical ownership

- [x] Accept only the unfrozen `interface/cli/` subset and canonical warehouse schema owner;
  leave delegated `interface/cli/data.py`, `parquet_ops/`, `pyproject.toml`, offline builders,
  readers, and direct-writer surfaces untouched.
- [x] Extract the six approved forecast workflows and all shared chunk-lane framework types into
  `execution/`, preserving command names, parsing, payloads and Click error translation.
- [x] Publish typed snapshot schema descriptors from the existing warehouse registration owner.
- [x] Hand off snapshot receipt/finalization consolidation and registry/layout/coverage/row-reader
  decomposition to the frozen offline-builder and reader owners, with their exact acceptance gates;
  this bounded closure does not claim those owner-controlled refactors.

## Wave C3 — removals and quarantine

- [x] Delete confirmed debug/source orphans and remove the proven dependency closure
  (2026-09-02; the September 11 inventory confirms the sources, direct manifests and locked graph
  remain clean). Current image refusal is solely the serialized quality-receipt handoff below.
- [x] Resolve UI, `planes/`, deprecated endpoint and one-shot-module candidates through runtime and
  external-consumer evidence; retain with a blocker or remove, never leave an unlabeled candidate.
- [x] Remove commented-out Compose blocks (2026-09-02). Scheduler config retirement stays in the
  gapless-publication track, and Drizzle files stay owned by shrink `s6`.

## Wave C4 — integrated verdict

- [x] Reconcile Railway/database topology documentation from one read-only inventory (2026-09-02, from the scheduler handoff evidence).
- [x] Publish an explicit dormant-migration evidence manifest with state, reason and production
  fingerprint (2026-09-02, `evidence/dormant-migrations.md`); any migration edit or movement requires a shrink `s6` handoff.
- [x] Reconcile the Python guide's least-privilege checklist with its recorded DSN-custody retirement (2026-09-02).
- [x] Run the final exact-tree frontend/Python/type/lint/test/build sweep. Frontend and Python
  gates passed; both image builds reached and correctly refused the offline-owner-serialized stale
  `QUALITY_RECEIPT.json`, which must be refreshed by that owner's end-of-wave sweep.
- [x] Obtain separate review and publish retained/removal evidence plus rollback notes. The first
  review blocked on SQL resource ownership; after the four forecast resources moved with their
  execution call site, the independent re-review approved the bounded implementation.

## September 11 bounded closure

The implementation write set is deliberately smaller than the 2026-09-02 planned ownership.
Current owner freezes supersede that proposed partition. The reader-owned `parquet_ops` files,
offline snapshot builders, generic availability/scheduler registries, migration/history files,
frontend renderer/readers, and non-forecast product transaction sites remain in their owning
tracks. Completion here means the safe conformity slice is implemented and independently
verified; the track stays `active` until the registry owner reconciles those recorded handoffs.
