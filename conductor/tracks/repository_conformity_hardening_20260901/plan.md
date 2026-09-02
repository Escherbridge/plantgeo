---
type: track-plan
slug: repository_conformity_hardening_20260901
status: planned
resource: ./spec.md
---

# Plan

## Wave C0 — safety and evidence freeze

- [ ] Remove the fabricated moderation scorecard; show unavailable evidence honestly.
- [ ] Store the audit inventory with each candidate classified as immediate, confirmed, contingent,
  refactor, enforcement gap, or protected evidence.
- [ ] Freeze file ownership with the four active Parquet tracks before shared edits.

## Wave C1 — executable standards

- [ ] Enable an intentional TypeScript unused-symbol policy and clear its production findings.
- [ ] Extend Python typing/architecture checks to operator scripts and thin adapters.
- [ ] Add a locked Python quality receipt to the production build/deploy path.

## Wave C2 — canonical ownership

- [ ] Accept `interface/cli/`, `parquet_ops/` and `pyproject.toml` only after shrink `s2a` is
  frozen; do not edit the delegated `s2b` direct-writer surface.
- [ ] Extract CLI-owned execution framework and workflows into domain packages one command group at
  a time, preserving help, names, outputs and exit codes.
- [ ] Extract one typed snapshot-breakdown/receipt core with product-specific policies.
- [ ] Split snapshot registry, layout validation, coverage and row-read responsibilities while
  retaining one public API and one schema descriptor.

## Wave C3 — removals and quarantine

- [ ] Delete confirmed debug/source orphans and direct dependencies after their proof gates.
- [ ] Resolve UI, `planes/`, deprecated endpoint and one-shot-module candidates through runtime and
  external-consumer evidence; retain with a blocker or remove, never leave an unlabeled candidate.
- [ ] Remove commented-out Compose blocks. Scheduler config retirement stays in the
  gapless-publication track, and Drizzle files stay owned by shrink `s6`.

## Wave C4 — integrated verdict

- [ ] Reconcile Railway/database topology documentation from one read-only inventory.
- [ ] Publish an explicit dormant-migration evidence manifest with state, reason and production
  fingerprint; any migration edit or movement requires a shrink `s6` handoff.
- [ ] Reconcile the Python guide's least-privilege checklist with its recorded DSN-custody retirement.
- [ ] Run the single final frontend/Python/type/lint/test/build sweep on the exact tree.
- [ ] Obtain separate review and publish retained/removal evidence plus rollback notes.
