# Conductor Track Execution Plan: Data Warehouse Materialized Zoom Aggregation

## Phase 1: Database Migration & Materialized Views
- Author Drizzle SQL migration creating Materialized Views for zoom aggregation:
  - `geo.mv_soil_field_coarse` (0.5° grid binning)
  - `geo.mv_soil_field_regional` (0.25° grid binning)
  - `geo.mv_climate_field_coarse` (0.5° grid binning)
- Add unique compound indexes on `(observed_day, cell_id)` to enable `REFRESH MATERIALIZED VIEW CONCURRENTLY`.
- Add GIST spatial indexes `USING GIST(geom)` on geometry columns for sub-millisecond bounding box queries.

## Phase 2: Read Model Service Update
- Update `src/lib/server/services/environmental-read-model.ts`:
  - Route low-zoom requests (`zoom <= 6`) to `geo.mv_*_coarse`.
  - Route mid-zoom requests (`7 <= zoom <= 11`) to `geo.mv_*_regional`.
  - Route high-zoom requests (`zoom >= 12`) to `geo.features` / detail tables bounded strictly by viewport `bbox`.
- Apply strict PostGIS `ST_Clip` and `ST_SimplifyPreserveTopology` bounds to ensure responses never exceed safe memory limits.

## Phase 3: Targeted Feature Verification
- Run feature-targeted query benchmark: execute single procedure test `npx vitest run src/__tests__/services/zoom-materialized-views.test.ts`.
