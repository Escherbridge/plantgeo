---
type: track-plan
track: botanical_occurrence_experience_20260911
status: active
---

# Plan

## September 14 implementation and QA intake

The occurrence components are now mounted; the [long-horizon QA session](../platform_experience_qa_20260911/evidence/runbook-session-20260914.md)
adds GBIF zoom-floor, honest empty-result and transport-failure feedback. Source admission,
viewport support policy, source-specific provenance and full browser/agent acceptance remain open.
The earlier planning checklist below is historical planning context, not evidence that the mounted
components are absent or accepted.

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
