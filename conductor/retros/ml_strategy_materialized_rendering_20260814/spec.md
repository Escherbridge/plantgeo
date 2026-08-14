---
type: specification
---

# Conductor Track Specification: ML Strategy Materialized View Rendering Engine

## Track ID: `ml_strategy_materialized_rendering_20260814`

### Overview
This track operationalizes the rendering pipeline for Machine Learning Strategy Selections (Regenerative Agriculture, Agroforestry Shelterbelts, Biochar Soil Enhancement, Wildfire Prevention Corridors, Drought & Water Conservation) by adhering strictly to the **PostGIS Materialized View Zoom Aggregation Pattern** established in `dw_materialized_zoom_aggregation_20260814`.

Instead of running heavy causal model predictions or spatial grid convolutions during MapLibre vector tile requests, this track pre-computes strategy feasibility, expected causal effect bounds ($\hat{\tau}$), and optimal land practice assignments into zoom-tiered PostGIS Materialized Views (`geo.mv_strategy_recommendations_*`) with GIST spatial indexes and concurrent refresh routines.

### Objectives
1. Implement 3 PostGIS Materialized View zoom tiers for ML strategy recommendations:
   - `geo.mv_strategy_recommendations_coarse` (0.5° grid, `zoom <= 6`): Macro-regional strategy suitability & dominant practice heatmaps.
   - `geo.mv_strategy_recommendations_regional` (0.25° grid, `7 <= zoom <= 11`): Sub-basin & watershed strategy recommendations with confidence bounds.
   - `geo.mv_strategy_recommendations_detail` (0.05° grid / parcel centroids, `zoom >= 12`): High-resolution parcel practice assignments (e.g. keyline plowing, agroforestry buffer placement, biochar rate).
2. Add unique compound indexes `(strategy_id, cell_id)` on all Materialized Views to enable non-blocking `REFRESH MATERIALIZED VIEW CONCURRENTLY`.
3. Add GIST spatial indexes `USING GIST(geom)` for sub-millisecond MapLibre vector tile queries via Martin MVT server (`geo.strategy_recommendations_tiles()`).
4. Integrate strategy layers into `src/lib/map/layer-registry.ts` and `src/lib/server/services/environmental-read-model.ts`.

### Key Deliverables
- **PostgreSQL DDL Migration**: Materialized views `geo.mv_strategy_recommendations_coarse`, `geo.mv_strategy_recommendations_regional`, and `geo.mv_strategy_recommendations_detail` with compound unique and GIST spatial indexes.
- **Martin MVT Tile Function**: PostGIS SQL function `geo.strategy_recommendations_tiles(z, x, y)` routing tile requests to the appropriate zoom materialized view.
- **Read Model Route Update**: Integration in `environmental-read-model.ts` mapping UI zoom levels to strategy materialized views.
- **Map Manager Layer Registration**: Registration of `strategy-recommendations` layer in `layer-registry.ts` with legend color ramps for Regenerative Ag, Agroforestry, Biochar, and Wildfire Mitigation.
- **Automated Verification**: Vitest query benchmark testing vector tile resolution and sub-10ms bounding box lookups.
