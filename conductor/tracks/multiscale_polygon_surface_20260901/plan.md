---
type: track-plan
slug: multiscale_polygon_surface_20260901
status: active
resource: ./spec.md
---

# Plan

## Retained checkpoint — September 11

Support and renderer implementation are complete subsets. The
[MTBS rollout](../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
adds one bounded production/browser result, including the proved 746-versus-747
viewport geometry distinction. It does not close the cross-product M3 conservation,
pixel-continuity, mobile or request-to-paint matrix below.

## Wave M0 — contract freeze

- [x] Enumerate every production layer's render class and permitted form at each zoom rung
  (2026-09-02, `src/lib/map/layer-render-contract.ts`; two rulings owed: `isoband` for the four
  isoline-withheld signals, and whether `weather` is stations or a sampled grid).
- [x] Freeze `supportKind`, rung, cell geometry and aggregation metadata with the reader track (2026-09-02, `AggregateEnvelopeSupport` incl. `cellOriginDegrees`).
- [ ] Capture MTBS, fire, air-temperature and soil-moisture production visual baselines.

## Wave M1 — serving geometry

- [x] Return explicit support polygons/extents and stable IDs for aggregate cells (2026-09-02).
- [x] Make climate reads select the actual zoom rung rather than pinning the fine rung
  (2026-09-02, reader r2b: `getClimateField` takes `zoom`; the map draws the served form).
- [x] Add rung-conservation and support-geometry contract tests (2026-09-02: lattice domain sweep, conservation test).

## Wave M2 — parallel renderer implementation

- [x] Build continuous-field tessellations/filled isobands with dissolved seams (2026-09-02).
- [x] Build event density cells, heatmaps or clusters with raw detail points (2026-09-02: fire density cells, water mean-flow cells, vegetation cells).
- [x] Verify native polygon generalization without changing identity or topology (2026-09-02: regression test + `evidence/native-polygon-baseline.md`; soil-survey coarse summary recorded as a deviation).
- [ ] Keep the climate/soil and event renderer ownership disjoint.

## Wave M3 — integrated visual verification

- [ ] Verify no cracks, nested blocks or simultaneous rungs at default PNW zoom transitions.
- [ ] Reconcile aggregate counts/sums against the detail rung.
- [ ] Record response size, feature count and request-to-paint budgets.
- [ ] Run screenshot and canvas-pixel checks on desktop and mobile viewports.
- [ ] Submit the exact renderer packet to `parquet_production_acceptance_20260901`.

## September 12 — bounded scalar label follow-up

The local [scalar-label receipt](evidence/scalar-labels-20260912/README.md) adds
reusable numeric annotations to the existing climate and soil scalar renderers.
It includes independent source review and synthetic desktop/mobile canvas
captures at all four zoom rungs. It does not close M3: live cross-product
conservation, dense basemap/hover interaction, published-day transitions and
request-to-paint budgets remain open. Weather remains the integrated repair;
vegetation and soil-survey contract limits are documented rather than widened.

## September 12 — current local evidence boundary

The [renderer proof and blocker packet](evidence/renderer-local-proof-boundary-20260912.md)
reconciles the scalar subset against local main `843b4b3`. Its implementation is
already integrated through `ac4ce70`; the platform plan's earlier author-handoff
wording is stale. This evidence-only continuation makes no M3 acceptance claim.

The [retained-artifact audit](evidence/retained-scalar-artifact-audit-20260912.json)
rehashes the 26 original PNGs and independently recomputes their recorded RGB
pixel counts. All counts match. It performs no fresh browser execution. The
fixture unmounts before each case: empty/zero-opacity frames are initial states,
and the reload images show mounting after style replacement, not recovery of a
mounted layer. The 390px captures contain no touch input. Mounted lifecycle,
stale-day and picking unit contracts are separately inventoried as inspected,
not newly passed. Only the two documented interior seam probes support the
existing narrow continuity claim.

The current checkout lacks the renderer/test dependencies. The packet names the
local fixture improvements, governed data/reader handoff, climate/soil picking
contract, numeric budget freeze and browser/device prerequisites required for
fresh desktop/mobile proof. M0 baseline completion, every M3 checkbox and QA
MS-01 through MS-04 remain open; both parent tracks stay active.

## September 12 — isolated vegetation scalar field candidate

The [vegetation scalar field packet](evidence/scalar-field-vegetation-20260912.md)
reconciles the archived dot-renderer audit with the current measured-cell contract.
The local candidate adds a default-off reusable nearest-cell WebGL2 paint path,
projected-spacing inspection cues, and exact native-cell hover/tap metadata.
Vegetation remains discrete; climate and weather ownership remain unchanged.
This packet is a bounded local candidate and does not close any open M3 gate.
