---
type: track-plan
slug: multiscale_polygon_surface_20260901
status: planned
resource: ./spec.md
---

# Plan

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
