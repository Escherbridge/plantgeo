---
type: track-plan
slug: parquet_reader_cutover_acceptance_20260901
status: active
resource: ./spec.md
---

# Plan

## Current checkpoint — September 12

The [local availability contract reconciliation](evidence/local-availability-contract-20260912.md)
freezes the current coverage v3 wire, checksum/refusal semantics and actual cache/policy
boundaries against `8c14ea0117ba14d5584f737b793f989566102514`. This closes only the local
R0 contract-definition item. The default transitional policy still permits census for
never-bootstrapped lanes; static lookups use census under both policies. Rollup hits,
full-index reads and bootstrap-marker checks have different request classes. The
original literal pair-of-GETs gate remains unmeasured and must be reconciled in its
production trace. The [root integrated receipt](../platform_experience_qa_20260911/evidence/root-integrated-checks-20260912.md)
records earlier validation and environment limits, including the missing local
frontend toolchain; it does not certify this new documentation candidate. This
continuation's final handoff owns its independent documentation review, validation
and exact commit/tree; the root [task ledger](../platform_experience_qa_20260911/evidence/task-ledger.md)
retains ownership. No fresh runtime pass or production policy is claimed here.
The UI provenance/ceiling caption remains open.

The [MTBS rollout](../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
records deployed `fa20223`, selected-day public/browser checks and the prior
frontend release sweep. The [completed-cutover verification](../../retros/parquet_cutover_completed_slices_20260910/verification.md)
records later cleanup checks. These supersede “no later sweep exists” as a general
claim; the unchecked final handoff still requires the exact current all-reader
packet. Original wave comments retain their dates. The support contract was
consumed by multiscale implementation on September 2.

## Wave R0 — evidence and wire freeze

- [ ] Capture the current request route and one cold/warm timing packet at coarse, middle and detail zoom.
- [x] Freeze temporal state, rung, support kind, cell extent/resolution, receipts and truncation
  (`src/lib/map/layer-render-contract.ts`, historical `coverage_schema_version` 2;
  2026-09-02). Current coverage wire v3 adds `latest_recorded_day`; see the September 12 receipt.
- [x] Freeze the availability-index wire, checksum/ETag cache behavior and fail-closed response when
  a lane has not yet published its index (2026-09-12, source contract only;
  [receipt](evidence/local-availability-contract-20260912.md)). Conditional GET is absent;
  unpublished lanes fail closed under `availability`, while the transitional policy can
  census never-bootstrapped lanes. This check does not certify production bootstrap or policy.
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
- [ ] Surface `coverageAuthority` and `sourceCeilingDay` in the slider caption
      (`LayerRow`/`layer-coverage-track`) — published by the capability service 2026-09-02, no UI
      consumer yet. A row read from an object-store walk currently captions identically to one
      proved from the checksummed availability index, and a lane held back by its source's ceiling
      captions identically to one that is simply behind.
- [ ] Run the focused TypeScript, lint and reader suites once after all reader changes.
  Wave 1 was swept green (2026-09-02: tsc clean, eslint 0 errors, vitest 1,622 passed) — that
  result does NOT cover the r3 deletion wave, which was authored without running anything and
  is swept once at the parallel wave's join. Re-tick only against a fresh run.
- [ ] Obtain a separate reviewer verdict. Wave 1 has one (2026-09-02: two adversarial reviews
  CHANGES-REQUIRED, fixed, closure verified). The r3 deletion / agent-repoint wave has none yet;
  the authoring evidence is `evidence/reader-cutover-verdict.md` and the reviewer is a separate
  context, per `plantgeo-authoring-and-verification-are-separate-agents`.

## Handoff

- [x] Publish the exact commit, candidate, tests, request traces and rollback commit to the
  acceptance track (2026-09-02, `evidence/reader-cutover-verdict.md`: commits `2b4cfef` /
  `9052998` / this wave, per-gate test citations, the map and agent request shapes, and the
  `2b4cfef..HEAD` revert). Request TRACES are the one item not published — they are wall-clock
  production evidence, and gates 7 and 9 are handed to
  `parquet_production_acceptance_20260901` with the browser halves of gates 1 and 4.
- [x] Release the frozen support contract to the spatial-rendering track (consumed by
  multiscale M1/M2 on 2026-09-02; see that track's checked implementation items).
