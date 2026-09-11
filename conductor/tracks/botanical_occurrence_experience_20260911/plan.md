---
type: track-plan
track: botanical_occurrence_experience_20260911
status: planned
---

# Plan

No UI, service or data implementation is part of this planning pass.

## X0 — approve semantics

- [ ] Approve the names and user-facing definitions of specimen occurrences,
  documented-taxon richness and collection evidence/effort.
- [ ] Freeze snapshot-versus-event-time presentation, supported taxon filters,
  uncertainty classes, empty states and prohibited ecological claims.
- [ ] Bind every zero/outside/withheld/generalized/not-evaluated visual state to
  the data plane's support-evaluation artifact rather than client inference.
- [ ] Choose exact zoom bands and whether possible-membership records are hidden,
  separately styled or included only in coarse summaries.

## X1 — freeze wire and render contracts

- [ ] Receive the occurrence plane's release, taxon, event-window, bbox, zoom,
  caps, continuation and refusal schema.
- [ ] Define per-layer fields, legends, palettes, accessibility labels, tooltips,
  picking and feature budgets.
- [ ] Define the capability-catalogue and agent-tool schemas without introducing
  a second interpretation of the metrics.
- [ ] Expose canonical taxon and occurrence-release identities for composition
  with the nonspatial species-profile API; do not join editable database values
  or calculate recommendation suitability inside the map layer.
- [ ] Require explicit neighbour date/interval, temporal and spatial distances,
  uncertainty and no-silent-substitution response states.

## X2 — future implementation lanes

- [ ] Implement detail occurrences, richness and effort in disjoint files.
- [ ] Serialize shared registry/render-contract integration in one owner.
- [ ] Verify conservation, zoom exclusivity, empty/refusal behavior, desktop and
  mobile rendering, canvas pixels, performance and agent/UI parity.
- [ ] Obtain a separate scientific/design/accessibility/agent-parity review after
  integration; the shared integrator cannot issue its own acceptance verdict.

Implementation remains blocked on the source-admission and occurrence-plane
contracts even though this track's design work is planned.
