---
type: track-plan
slug: parquet_reader_cutover_acceptance_20260901
status: active
resource: ./spec.md
---

# Plan

## Current checkpoint — September 11

The [MTBS rollout](../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
records deployed `fa20223`, selected-day public/browser checks and the prior
frontend release sweep. The [completed-cutover verification](../../retros/parquet_cutover_completed_slices_20260910/verification.md)
records later cleanup checks. These supersede “no later sweep exists” as a general
claim; the unchecked final handoff still requires the exact current all-reader
packet. Original wave comments retain their dates. The support contract was
consumed by multiscale implementation on September 2.

The [September 11 reader handoff](evidence/reader-local-handoff-20260911.md) adds
the current inventory, bounded neighbour reads, terminal/day-binding corrections,
slider captions and candidate validation. The [route packet](evidence/reader-route-evidence-20260911.md)
contains 85 public row observations against deployed baseline `fa20223`, plus
capability and readiness observations. First/repeated timings do not establish
controlled cold/warm caches. This track remains active for the production gates
and explicit unsupported product semantics described in that handoff.

## Wave R0 — evidence and wire freeze

- [ ] Capture the current request route and one cold/warm timing packet at coarse, middle and detail zoom.
- [x] Freeze temporal state, rung, support kind, cell extent/resolution, receipts and truncation
  (`src/lib/map/layer-render-contract.ts`, `coverage_schema_version` 2; 2026-09-02).
- [x] Freeze the availability-index wire, checksum/ETag cache behavior and fail-closed response when
  a lane has not yet published its index (September 11 current implementation inventory and
  `evidence/reader-route-evidence-20260911.md` operation table). Live cache measurements remain
  separate below; static census is not falsely included in the zero-LIST invariant.
- [x] Record exact rollback and no-live-request evidence requirements before editing
  (2026-09-02, `evidence/reader-cutover-verdict.md` §Rollback and §gate 1; the static, unit and
  browser tiers of the no-live-request proof were first stated in `evidence/r1-fire-hard-cut.md`).

## Wave R1 — parallel reader changes

- [x] **R1a fire (2026-09-02, `2b4cfef`):** replace the legacy hook with `wildfire.getFireDetections`, passing settled day,
  bbox and zoom. Preserve previous painted data while the new request is pending, but never carry it
  as the answer for the new day.
- [x] **R1b catalogue (2026-09-02, `2b4cfef`; production flip gated on per-lane bootstrap):** reconcile all eligible capability entries with their actual reader, source
  ceiling and terminal-day semantics. Read the generational availability artifact; do not retain a
  historical listing/data-scan fallback.

R1a and R1b may run in parallel only while their file ownership remains disjoint. The Parquet client
and shared capability registry have one serialized owner.

## Wave R2 — legacy removal and focused verification

- [x] Prove parity and no-live-request evidence (2026-09-02, `evidence/reader-cutover-verdict.md`
  §gates 1–3; the tree-provable tiers only — the DevTools trace stays with the production track).
- [ ] Trace cold and warm capability reads and prove zero historical LIST/data-part operations.
  Gate 9's request COUNT is a production measurement; the tree half is recorded in
  `evidence/reader-cutover-verdict.md` §gate 9.
- [x] Delete or quarantine the obsolete fire REST route and hook (2026-09-02, deleted outright:
  `src/hooks/useFireData.ts`, `src/app/api/fires/route.ts` and their two suites). The last
  request-time PostgreSQL fire read went with them — `regional-context.ts` (the agent's read) now
  calls `getParquetFireDetections`; `getPublishedFireDetections` survives with one caller,
  `alert-engine.ts`, which is a server-side job and not a map or agent reader.
- [x] Surface `coverageAuthority` and `sourceCeilingDay` in the slider caption
      (September 11 `LayerTimeSlider` / `layer-coverage-track`): distinguish availability,
      object inventory and unstated authority, and name the source publication ceiling
      independently of legitimate carried days. An unpublished index has a settled unverified state.
- [x] Run the TypeScript, lint, boundary and reader suites against the final September 11
  candidate (`evidence/reader-local-handoff-20260911.md`, closure logs). Selectors required
  full frontend and Python test fallbacks. All final gates passed; skips and warning counts
  are retained. Earlier failed sweeps and the combined corrections are recorded explicitly.

  Historical checkpoint:
  Wave 1 was swept green (2026-09-02: tsc clean, eslint 0 errors, vitest 1,622 passed) — that
  result does NOT cover the r3 deletion wave, which was authored without running anything and
  is swept once at the parallel wave's join. Re-tick only against a fresh run.
- [x] Obtain a separate reviewer verdict (September 11
  `evidence/independent-reader-review-20260911.md`: local candidate approved,
  14 runtime hashes checked, production acceptance explicitly open).

  Historical checkpoint: Wave 1 has one (2026-09-02: two adversarial reviews
  CHANGES-REQUIRED, fixed, closure verified). The r3 deletion / agent-repoint wave has none yet;
  the authoring evidence is `evidence/reader-cutover-verdict.md` and the reviewer is a separate
  context, per `plantgeo-authoring-and-verification-are-separate-agents`.

## Handoff

- [x] Record the current local code candidate `d56b508fb3afe8adb85969ce1553cd739daf779f`,
  validation manifest, independent approval, 85 baseline public row observations,
  deployment identities and bounded remaining production packet in
  `evidence/reader-local-handoff-20260911.md`. This closes the local handoff,
  not the unchecked controlled traces or wider product acceptance.

The September 2 broad `2b4cfef..HEAD` rollback range below is historical only.
The current handoff supersedes it: reverse only the new reader candidate commits
through the normal release path after approval, never restore retired PostgreSQL readers.

- [x] Publish the exact commit, candidate, tests, request traces and rollback commit to the
  acceptance track (2026-09-02, `evidence/reader-cutover-verdict.md`: commits `2b4cfef` /
  `9052998` / this wave, per-gate test citations, the map and agent request shapes, and the
  `2b4cfef..HEAD` revert). Request TRACES are the one item not published — they are wall-clock
  production evidence, and gates 7 and 9 are handed to
  `parquet_production_acceptance_20260901` with the browser halves of gates 1 and 4.
- [x] Release the frozen support contract to the spatial-rendering track (consumed by
  multiscale M1/M2 on 2026-09-02; see that track's checked implementation items).
