---
type: track-plan
slug: gapless_parquet_publication_20260901
status: planned
resource: ./spec.md
---

# Plan

## Wave P0 — inventory and contract freeze

- [ ] Enumerate every time-bearing product, provider, floor, source ceiling, lag, cadence and rung.
- [ ] Record current, legacy and proposed executor ownership plus no-overlap handoff state.
- [ ] Re-list live coverage and incomplete ladders before authorizing any write.
- [ ] Freeze terminal-state, governed-absence and source-settlement rules.
- [ ] Freeze the generational availability schema, pointer contract, conditional-update behavior,
  bootstrap receipt and no-request-time-scan tripwire.

## Wave P0A — availability core and one-time bootstrap

- [ ] Implement one canonical availability schema, reader and publisher with content-addressed
  immutable generations and a checksum-bound pointer written last.
- [ ] Add an idempotent bootstrap command that consumes verified manifests/checkpoints once and
  writes an immutable receipt binding their keys/SHAs, the exact inventory root and authoritative
  required-rung set; it is never called by an HTTP or slider request.
- [ ] Prove pointer-race retry, correction generation, governed absence, all-rung intersection,
  malformed/checksum refusal and rollback behavior.

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
- [ ] Register executor schedules, leases, checkpoints, retries and dead-letter visibility.
- [ ] Document source-by-source pause, lease-expiry, activation and rollback procedures.

## Wave P3 — controlled production handoff

- [ ] Obtain separate explicit production authorization for the exact executor candidate and rollback.
- [ ] Prove the legacy owner inactive before activating one executor lane.
- [ ] Close the bounded observed tails without rewriting valid immutable days.
- [ ] Publish governed absences only with source receipts.
- [ ] Observe retry, restart and expired-lease recovery.

## Wave P4 — burn-in and handoff

- [ ] Record at least three consecutive scheduled advancements per activated lane.
- [ ] Reconcile manifests, receipts, coverage and rung conservation after advancement.
- [ ] Hand exact evidence to `parquet_production_acceptance_20260901`.
- [ ] Leave PostgreSQL retirement blocked in the existing shrink track.
