---
type: track-plan
track: pnw_blm_sources_20260920
status: in_progress
---

# Plan

See [the PR #10 review evidence](../pnw_land_data_delivery_20260920/evidence/pr10-review-deployment-20260921.md)
for the physical-ladder correction and separate quality/deployment acceptance gates.

- [x] Admit official OR/WA ownership, national SMA for Idaho, and BLM field-office sources.
- [x] Capture bounded complete snapshots with stable native identifiers and immutable provenance.
- [x] Publish source detail and usable generalized rungs through the shared lifecycle.
- [x] Implement recurring change detection and repair with current-snapshot temporal semantics.
- [x] Validate source geometry, relationships and production publication.
- [x] Close the current independent PR review's physical publication-ladder findings and record final validation.
- [ ] Verify the merged revision is deployed and all three BLM products are readable through the live data service.
- [ ] Activate `land-context-blm-forward`, `land-context-blm-reconcile` and `land-context-blm-backfill`, preserving existing active lanes.
- [ ] Collect each BLM definition's first successful scheduled turn before closing this track.

[Production evidence](../pnw_land_data_delivery_20260920/evidence/production-delivery-20260921.md)
records 39,630 detailed boundaries, 34 office jurisdictions and 34 documented office inquiry
records. These are initial-publication receipts; deployment and schedule acceptance remain
pending. Inquiry records retain unverified route status and do not establish a responsible
individual or documented program contact. Additional grazing, wilderness, recreation and mineral interests remain in
[the deferred source track](../pnw_land_sources_deferred_20260920/plan.md).
