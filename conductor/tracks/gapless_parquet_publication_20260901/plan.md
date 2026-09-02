---
type: track-plan
slug: gapless_parquet_publication_20260901
status: active
resource: ./spec.md
---

# Plan

## Wave P0 — inventory and contract freeze

- [x] Enumerate the 28 current time-bearing physical product identities and canonical rung set.
- [ ] Freeze every provider floor, receipt-derived source ceiling, lag and cadence; the offline census
  records the unresolved measurements explicitly.
- [x] Record current, legacy and proposed executor ownership plus no-overlap handoff state.
- [ ] Re-list live coverage and incomplete ladders before authorizing any write.
- [x] Freeze terminal-state, governed-absence and source-settlement rules.
- [x] Freeze the generational availability schema, pointer contract, conditional-update behavior,
  bootstrap receipt and no-request-time-scan tripwire.

## Wave P0A — availability core and one-time bootstrap

- [x] Implement one canonical availability schema, reader and publisher with content-addressed
  immutable generations, typed evidence cross-binding, bounded reads, pre-CAS evidence-identity
  revalidation, a lane-wide shared/exclusive publication barrier and a checksum-bound pointer written
  last. The final integrated Python gate and independent review passed.
- [x] Add an idempotent bootstrap command that consumes verified manifests/checkpoints once and
  writes an immutable receipt binding their keys/SHAs, the exact inventory root and authoritative
  required-rung set; it is never called by an HTTP or slider request. No production bootstrap ran.
- [x] Prove typed receipt cross-binding, evidence mutation refusal, bounded object reads,
  pointer-race retry, correction generation, governed absence, all-rung completeness,
  malformed/checksum refusal, rollback behavior and writer/publication exclusion. The amended
  implementation passed the root-coordinated full integrated Python gate and separate review.

## Wave P1 — parallel source-family writers

- [ ] Build climate direct writers, solar first, within one bounded source-family owner.
- [ ] Build NASA POWER and ERA5-Land soil/product writers in disjoint ownership lanes.
- [ ] Reconcile existing fire, water, vegetation, weather and sensor writer ownership.
- [ ] Register each product only after its source-family review passes and its writer extends the
  availability generation after terminal publication.

## Wave P2 — repair and scheduler integration

- [ ] Author missing-day work and bounded idempotent repair.
- [ ] Require every required rung before terminal publication.
- [ ] Extend availability for published and governed-absence outcomes without rescanning history.
- [x] Register executor schedules, leases, checkpoints, retries and dead-letter visibility.
- [x] Document source-by-source pause, lease-expiry, activation and rollback procedures.
- [x] Inventory every live Railway scheduled/one-shot writer and bind its exact command, cadence,
      source settlement, checkpoint, lease, retry/dead-letter and rollback to the executor registry.
- [x] Retire `infra/cron-ingest/railway.json` and its cron-only Dockerfile,
      `infra/cron-mtbs/railway.json`, `infra/cron-soilgrids/railway.json` and its cron-only
      Dockerfile, `infra/parquet-drain/railway.json`, both direct-forward Railway configs, and add a
      guard that rejects any tracked `cronSchedule` resurrection.
- [x] Preserve the dedicated continuous `railway.job-executor.json`; record the scheduler handoff in
      `evidence/scheduler-handoff-20260902.md`.

## Wave P3 — controlled production handoff

- [x] Record the 2026-09-02 owner authorization for executor-only scheduling and cron-object removal.
- [x] Merge the reviewed release to `main` and prove the exact executor deployment is `SUCCESS`.
      Release `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1` is active in deployment
      `b1f35a20-6e05-48ff-9801-5235c9753a01`.
- [x] Receive the explicit orchestration follow-up, then prove all legacy owners and executor leases
      inactive before activation.
- [x] Fence all six legacy service objects with null schedules, no-op commands and `NEVER` restart;
      verify `scheduled=[]` across the production environment and activate 37 executable lanes.
- [ ] Repair 200 pre-existing `matview-refresh` dead letters caused by absent
      `geo.mv_feature_observation_day_axis` and `geo.mv_signal_cell_daily`; repair the WFIGS payload
      bound that placed `postgres-fire-perimeters` in retry backoff.
- [ ] Close the bounded observed tails without rewriting valid immutable days.
- [ ] Publish governed absences only with source receipts.
- [ ] Observe retry, restart and expired-lease recovery.
- [ ] Remove eligible legacy writer objects after mapped-lane proof, capture one removal receipt per
      service, and re-read the complete production service/deployment matrix. Keep the inert ingest
      object until its hidden CDS credentials and service-reference variables move to stable owners.
- [ ] Exercise rollback by disabling an executor lane; never restore a Railway cron schedule/service.

## Wave P4 — burn-in and handoff

- [ ] Record at least three consecutive scheduled advancements per activated lane.
- [ ] Reconcile manifests, receipts, coverage and rung conservation after advancement.
- [ ] Hand exact evidence to `parquet_production_acceptance_20260901`.
- [ ] Leave PostgreSQL retirement blocked in the existing shrink track.
