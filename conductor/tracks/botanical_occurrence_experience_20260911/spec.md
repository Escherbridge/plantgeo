---
type: track-spec
slug: botanical_occurrence_experience_20260911
status: planned
---

# Botanical occurrence map layers and agent experience

## Outcome

Expose governed herbarium evidence through three complementary layers and an
agent/tool surface. All views use the same pinned source-release set, taxonomy
version, QC policy, event filter and spatial support. The experience must show
what the collection can document and where its sampling limits the conclusion.

No layer or component is implemented by this planning pass.

## Layer 1 — botanical specimen occurrences

At detail zoom, show admitted public specimen locations or their declared
uncertainty support. A selected feature may expose taxon/determination, collecting
date or interval, collector/catalog reference, contributing collection, coordinate
uncertainty, source snapshot and rights/provenance link.

Do not place nonspatial records at an administrative centroid. Do not reveal or
infer withheld localities. A point means a documented specimen record at the
represented support; it does not prove current occupancy.

## Layer 2 — documented-taxon richness

At regional and middle zoom, show the exact count of distinct admitted taxon
concepts documented in each support cell for the selected collecting-event
window. The legend and tooltip must say **documented taxa**, name the source
snapshot and expose the number of records, contributing collections and records
excluded by uncertainty/QC.

Richness is recomputed from mergeable exact concept membership at each support.
Never sum child-cell richness, zero-fill unsampled taxa or interpret a blank cell
as botanical absence. Visually distinguish `zero documented records` from
`outside admitted coverage`, `withheld/generalized` and `not evaluated`.
These states come from the governed support-evaluation artifact; the client must
not infer them from an empty aggregate response.

## Layer 3 — collection evidence and effort

Show the observation process that can bias the richness view: specimen-record
count, separately defined collecting-event estimate, contributing-collection
count, date completeness and coordinate-uncertainty distribution. This is a
context layer, not a vegetation-density or abundance heatmap.

Use a measure whose aggregation is declared. Broad cells must not make dense
urban or roadside collecting look like ecological dominance. The default
richness view should make this layer or equivalent disclosure easy to reach.

## Filters, time and legends

Filters may include admitted taxon concept/rank/group, collection, public spatial
quality and collecting-event interval. The UI displays source snapshot/version
separately from event dates. It must not reuse a daily environmental slider in a
way that claims daily ecological availability. Partial and interval dates remain
visible as such.

Zoom transitions are mutually exclusive: aggregate support at lower zoom and
inspectable occurrences only at admitted detail. Each view declares record caps,
truncation/continuation, release and QC identity, and whether uncertain records
contribute as confirmed, possible or excluded membership.

## Agent and analysis experience

Agent tools answer only from the UI-selected release/filter context. Supported
answers include documented taxa/records in a bounded region, nearest represented
specimen supports with uncertainty-aware distance, collection/event-time
neighbours, contributing sources and explicit missingness/QC counts. Temporal
neighbours include the requested interval, the record's own event date or
interval and real distance in days where defined. Spatial neighbours include
represented support, uncertainty and real distance. A neighbour is labeled as a
substitute; it never silently answers an exact-result request.

The agent refuses abundance, percent cover, present occupancy, suitability,
surveyed absence and historical-publication claims that specimen evidence cannot
support. Any future trait, vegetation-community or distribution-model view needs
its own source, validation and track.

## Acceptance

Before implementation, approve the three layer contracts, their exact metrics,
visual hierarchy, zoom bands, empty/refusal states, legends, accessibility and
agent response schema. Final implementation will require count conservation,
no simultaneous rungs, desktop/mobile screenshots, canvas checks, request and
feature budgets, source-record drill-through and independent scientific/design
review.

The final scientific, design, accessibility and agent-parity verdict belongs to
an independent task context after the three layer authors and shared integrator
finish. An author or integration slice cannot approve its own claims.
