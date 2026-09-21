---
type: track-plan
track: pnw_crop_cover_20260920
status: in_progress
---

# Plan

See [the PR #10 review evidence](../pnw_land_data_delivery_20260920/evidence/pr10-review-deployment-20260921.md)
for the indexed physical-ladder correction and separate quality/deployment acceptance gates.

- [x] Admit official 2022–2025 raster items and publication dates.
- [x] Capture bounded raw-class raster tiles and immutable manifests.
- [x] Aggregate complete pixels with explicit no-data denominator and crop legend.
- [x] Publish conserved coarser rungs with observed year separate from release and capture clocks.
- [x] Integrate selected-day reads and truthful estimation labels.
- [x] Validate source captures, area conservation and production readback.
- [x] Close the current independent PR review's physical publication-ladder findings and record final validation.
- [ ] Verify the merged revision is deployed and all four admitted editions are readable through the live data service.
- [ ] Verify all four admitted editions and their controls in the live browser.
- [ ] Activate `crop-cover-usda-maintain`, preserving existing active lanes.
- [ ] Collect crop maintenance's first successful scheduled turn before closing this track.

[Production evidence](../pnw_land_data_delivery_20260920/evidence/production-delivery-20260921.md)
records all four published editions, their immutable source manifests, conserved ladders and
verified selected-release reads using the branch code. Deployment and schedule acceptance
remain pending. Native 10-m regional processing, older history and future
crop-year admissions remain separate follow-up work.
