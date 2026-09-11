---
type: track-evidence
slug: multiscale_polygon_surface_20260901
slice: m5
status: partial
observed_at: 2026-09-11
---

# Renderer verdict — September 11

## Result

The renderer boundary is stronger, but M5 is not closed. The local shell loaded at
`http://localhost:3001` on desktop with the map canvas, controls, manager entry point and
attribution present. This checkout has no configured authenticated governed-data plane, so that
observation proves local rendering startup only; it is not a production data, response-budget or
visual-continuity receipt.

## Renderer checks completed locally

- Fire now withholds a collection that mixes published rungs, so polygon density cells and detail
  dots cannot paint from one malformed frame. It also withholds a non-detail aggregate whose
  declared support has no polygon, rather than restyling the aggregate as a raw point.
- Water withholds only malformed non-raw aggregate cells without declared support geometry. A valid
  anonymous `raw_point` at z13 remains a point, preserving its declared source form without
  inventing a station identity.
- Climate no longer has a point renderer. An unexpected legacy `symbol` response clears the layer
  instead of rendering a continuous field as dots.
- Regression coverage exercises the malformed fire support, mixed fire tiers, malformed water
  aggregate, anonymous z13 water observation, and unexpected climate form paths.
- Separate renderer review approved the corrected diff after catching and resolving the anonymous
  z13-water regression. `git diff --check`, data-boundary verification, TypeScript checking and
  lint completed locally without a reported error.

## Evidence that remains required

The following cannot be claimed from the empty local browser shell and remains the exact
deploy/read-surface gate:

| Gate | Required matrix |
| --- | --- |
| Visual continuity | Desktop and mobile canvas-pixel plus screenshot checks at coarse, middle and detail transitions for MTBS, fire density, air temperature and soil moisture. |
| Semantics | Fire density caption visibly distinguishes a cell from a perimeter; native MTBS identity/topology; explicit empty, refusal and governed-absence states. |
| Conservation | Independently supplied detail versus aggregate counts/sums for each applicable product; do not treat continuous-field values or native geometry as additive counts. |
| Budgets | Per response bytes, feature count, cold/warm request count and response-to-first-paint at the default PNW camera, with served day/rung/support form recorded. |

Known product decisions remain open rather than hidden: soil-survey's wide-view count summary is a
recorded native-polygon deviation, and MTBS/fire-perimeter/evacuation layers have a z4 visibility
floor whose z2 payload and product decision are still required.
