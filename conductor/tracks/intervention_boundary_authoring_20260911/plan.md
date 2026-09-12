---
type: track-plan
track: intervention_boundary_authoring_20260911
status: active
---

# Plan

- [x] Implement validated polygon/rectangle/explicit-point draft geometry.
- [x] Mount preview and drawing controls in the contributor form; preserve form and cancel state.
- [x] Add keyboard/touch input, undo/clear/edit, exclusive gestures and style restoration.
- [x] Verify drawing behavior and integrate canonical local Polygon submission acceptance.
- [x] Run final integrated checks and obtain independent review.
- [ ] Commit candidate and hand off exact revision/shared patches to integration.

Candidate acceptance: 2,328 tests passed, including eight real PostGIS cases;
three browser cases passed; type/lint/data-boundary gates passed; separate review
accepted. See [verification evidence](evidence/review-and-verification.md).
The final handoff supplies its containing commit/tree and closes the last item.
Integration and deployment are not claimed by this local candidate.

Base: `89e8494422b8232c8f16dbffdcf2321c7ea17bc8`.
Shared MapView ownership is limited to the separately approved two-line publication
sync mount; boundary drawing itself mounts from the existing recommendation modal.
LayerManager and shared registries remain integration-owned.
