---
type: track-plan
slug: parquet_production_acceptance_20260901
status: blocked
resource: ./spec.md
---

# Plan

## Wave A0 — prepare while authoring runs

- [ ] Freeze the complete product/day/zoom/cold-warm matrix.
- [ ] Define evidence filenames, capture timestamps and exact commit/service identity.
- [ ] Define RED stop conditions and route each one to an owning track.

## Wave A1 — read-only route and data acceptance

- [ ] Probe coverage, day, window and release/refusal behavior against production R2.
- [ ] Verify every time-bearing lane's `_LATEST.json` binding and availability Parquet generation;
  validate the bound bootstrap receipt/inventory root and required-rung set, then trace cold/warm
  capability reads and prove zero historical LIST/data-part operations.
- [ ] Verify manifest and completion receipts, bbox bounds, rung inventory and `truncated=false`.
- [ ] Reconcile published versus governed-absence versus missing-day states.

## Wave A2 — browser and spatial acceptance

- [ ] Measure cold/warm catalogue time, TTFB and request-to-paint separately.
- [ ] Verify latest, historical and governed-empty days at coarse/middle/detail zoom.
- [ ] Assert no live fire request reaches `/api/fires`.
- [ ] Run screenshot and canvas-pixel seam checks plus rung-conservation checks.

## Wave A3 — scheduler burn-in

- [ ] Observe at least three consecutive scheduled advances for every activated product.
- [ ] Exercise retry, restart and expired-lease recovery without overlapping writers.
- [ ] Reconcile capability ceilings and inventories after every observed interval.

## Wave A4 — final verdict

- [ ] Run the single consolidated lint/typecheck/test sweep on the exact deployed tree.
- [ ] Obtain an independent review of code and evidence.
- [ ] Publish the exact service/deployment/commit/product/rollback matrix.
- [ ] State GREEN or RED and the precise PostgreSQL-retirement authorization status.
