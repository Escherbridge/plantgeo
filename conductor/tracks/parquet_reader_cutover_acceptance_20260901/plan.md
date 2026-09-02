---
type: track-plan
slug: parquet_reader_cutover_acceptance_20260901
status: planned
resource: ./spec.md
---

# Plan

## Wave R0 — evidence and wire freeze

- [ ] Capture the current request route and one cold/warm timing packet at coarse, middle and detail zoom.
- [ ] Freeze temporal state, rung, support kind, cell extent/resolution, receipts and truncation.
- [ ] Freeze the availability-index wire, checksum/ETag cache behavior and fail-closed response when
  a lane has not yet published its index.
- [ ] Record exact rollback and no-live-request evidence requirements before editing.

## Wave R1 — parallel reader changes

- [ ] **R1a fire:** replace the legacy hook with `wildfire.getFireDetections`, passing settled day,
  bbox and zoom. Preserve previous painted data while the new request is pending, but never carry it
  as the answer for the new day.
- [ ] **R1b catalogue:** reconcile all eligible capability entries with their actual reader, source
  ceiling and terminal-day semantics. Read the generational availability artifact; do not retain a
  historical listing/data-scan fallback.

R1a and R1b may run in parallel only while their file ownership remains disjoint. The Parquet client
and shared capability registry have one serialized owner.

## Wave R2 — legacy removal and focused verification

- [ ] Prove parity and no-live-request evidence.
- [ ] Trace cold and warm capability reads and prove zero historical LIST/data-part operations.
- [ ] Delete or quarantine the obsolete fire REST route and hook.
- [ ] Run the focused TypeScript, lint and reader suites once after all reader changes.
- [ ] Obtain a separate reviewer verdict.

## Handoff

- [ ] Publish the exact commit, candidate, tests, request traces and rollback commit to the acceptance track.
- [ ] Release the frozen support contract to the spatial-rendering track.
