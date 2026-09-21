---
type: track-plan
track: pnw_land_data_delivery_20260920
status: in_progress
---

# Plan

- [x] Implement per-product publication availability and independent crop display.
- [x] Repair stable boundary/contact identity and preserve source provenance.
- [x] Bind PNW sources; retain explicit unavailability outside PNW.
- [x] Apply all implementation fixes, then run integrated checks and independent review.
- [x] Ingest validated products into the existing production warehouse; verify readback.
- [x] Open [PR #10](https://github.com/Escherbridge/plantgeo/pull/10) with ingestion evidence and explicit follow-up tracks.
- [ ] After merge/deployment, verify the live browser and activate the four new scheduled definitions.

Implementation and initial production ingestion are verified in
[the September 21 delivery evidence](evidence/production-delivery-20260921.md).
The track remains open for deployment and schedule acceptance; production data publication
does not imply that the unmerged frontend or executor definitions are deployed.
