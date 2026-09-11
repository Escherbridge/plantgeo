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
Remaining CLI/core extraction and removal candidates below still require their
own evidence. The thin-CLI strict xfail is still present; it is not a pass.

## Wave C0 — safety and evidence freeze

- [x] Remove the fabricated moderation scorecard; show unavailable evidence honestly
  (2026-09-02, `2b4cfef`; the fabricated `causalTauEst ?? 0.15` submission
  default was subsequently removed in `ad4e015`, with absent estimates omitted).
- [ ] Store the audit inventory with each candidate classified as immediate, confirmed, contingent,
  refactor, enforcement gap, or protected evidence.
- [ ] Freeze file ownership with the four active Parquet tracks before shared edits.

## Wave C1 — executable standards

- [x] Enable an intentional TypeScript unused-symbol policy and clear its production findings (2026-09-02, `12fa189`).
- [x] Extend Python typing/architecture checks to operator scripts and thin adapters (2026-09-02; the thin-adapter rule is pinned as a strict xfail until c2).
- [x] Add a locked Python quality receipt to the production build/deploy path (2026-09-02: `QUALITY_RECEIPT.json` + `scripts/verify_quality_receipt.py` in both runtime Dockerfiles).

## Wave C2 — canonical ownership

- [ ] Accept `interface/cli/`, `parquet_ops/` and `pyproject.toml` only after shrink `s2a` is
  frozen; do not edit the delegated `s2b` direct-writer surface.
- [ ] Extract CLI-owned execution framework and workflows into domain packages one command group at
  a time, preserving help, names, outputs and exit codes.
- [ ] Extract one typed snapshot-breakdown/receipt core with product-specific policies.
- [ ] Split snapshot registry, layout validation, coverage and row-read responsibilities while
  retaining one public API and one schema descriptor.

## Wave C3 — removals and quarantine

- [x] Delete confirmed debug/source orphans after their proof gates (2026-09-02; dependencies recorded removal-ready, not removed — needs lock regeneration and an image build).
- [ ] Resolve UI, `planes/`, deprecated endpoint and one-shot-module candidates through runtime and
  external-consumer evidence; retain with a blocker or remove, never leave an unlabeled candidate.
- [x] Remove commented-out Compose blocks (2026-09-02). Scheduler config retirement stays in the
  gapless-publication track, and Drizzle files stay owned by shrink `s6`.

## Wave C4 — integrated verdict

- [x] Reconcile Railway/database topology documentation from one read-only inventory (2026-09-02, from the scheduler handoff evidence).
- [x] Publish an explicit dormant-migration evidence manifest with state, reason and production
  fingerprint (2026-09-02, `evidence/dormant-migrations.md`); any migration edit or movement requires a shrink `s6` handoff.
- [x] Reconcile the Python guide's least-privilege checklist with its recorded DSN-custody retirement (2026-09-02).
- [ ] Run the single final frontend/Python/type/lint/test/build sweep on the exact tree.
- [ ] Obtain separate review and publish retained/removal evidence plus rollback notes.
