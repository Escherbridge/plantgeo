# Conductor Track Execution Plan: ML Strategy Materialized View Rendering Engine

## Phase 1: Materialized View Migration & Indexes
- [x] Author Drizzle/Alembic DDL migration creating ML Strategy Materialized Views:
  - `geo.mv_strategy_recommendations_coarse` (0.5° grid binning)
  - `geo.mv_strategy_recommendations_regional` (0.25° grid binning)
  - `geo.mv_strategy_recommendations_detail` (0.05° grid binning)
- [x] Add unique compound indexes on `(strategy_id, cell_id)` to enable `REFRESH MATERIALIZED VIEW CONCURRENTLY`.
- [x] Add GIST spatial indexes `USING GIST(geom)` on geometry columns for instant MapLibre vector tile fetching.

## Phase 2: Martin MVT Function & Read Model Integration
- [x] Create PostGIS tile function `geo.strategy_recommendations_tiles(z, x, y)` in DB migrations (`0027_ml_strategy_materialized_views.sql`):
  - Selects from `geo.mv_strategy_recommendations_coarse` when `z <= 6`.
  - Selects from `geo.mv_strategy_recommendations_regional` when `7 <= z <= 11`.
  - Selects from `geo.mv_strategy_recommendations_detail` when `z >= 12`.
  - Encodes MVT geometry using `ST_AsMVT()`.
- [x] Update `src/lib/server/services/environmental-read-model.ts` to expose strategy recommendations by zoom and bounding box.

## Phase 3: Layer Registry & Map UI Rendering
- [x] Add `strategy-recommendations` layer definition to `src/lib/map/layer-registry.ts`:
  - Categories: Regenerative Agriculture (Emerald), Agroforestry (Forest Green), Biochar Soil Retention (Amber/Bronze), Wildfire Buffer (Coral/Red), Water Conservation (Azure).
- [x] Add strategy layer toggle & component in `src/components/map/layers/StrategyLayer.tsx`.
- [x] Connect layer selection to `RegionalIntelligencePanel.tsx` so clicking any strategy cell highlights the recommended practice and causal evidence metrics.

## Phase 4: Automated Refresh Routine & Verification
- [x] Add automated refresh trigger into `services/agri-data-service/agri_data_service/jobs/scheduler.py` to run `REFRESH MATERIALIZED VIEW CONCURRENTLY geo.mv_strategy_recommendations_*` whenever new model weights or environmental streams land. *Implemented 2026-08-14 — `strategy-mv-refresh` (handler `jobs.strategy_mv_refresh`) in `jobs/strategy_mv_refresh.py`, registered on the real `agri.job_definition`/`agri.job_run` ledger and driven by `StrategyMvRefreshScheduler` + the `/api/v1/jobs/trigger` route in `jobs/scheduler.py`; earlier tick predated the implementation.*
- [x] Run single procedure test: `npx vitest run src/__tests__/services/strategy-materialized-views.test.ts`.
