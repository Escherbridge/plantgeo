# Conductor Track Specification: Data Warehouse Materialized Zoom Aggregation

## Track ID: `dw_materialized_zoom_aggregation_20260814`

### Overview
This track implements PostGIS Materialized Views for pre-aggregated geospatial layer zoom tiers (`coarse-average`, `regional-average`, `detail`). Instead of executing spatial grid convolutions and aggregations on the fly during every tRPC request, pre-aggregated spatial views store pre-computed spatial cells, centroids, and isolines with GIST spatial indexes.

### Objectives
1. Eliminate on-the-fly SQL grid convolutions during tRPC geospatial requests.
2. Standardize zoom-dependent payload density (~256 cells for `coarse-average`, ~512 cells for `regional-average`) across all geospatial endpoints (`soil-field`, `climate-field`, `drought`, `weather`, `water-gauges`).
3. Support `REFRESH MATERIALIZED VIEW CONCURRENTLY` so background data ingest updates never lock user reads.
4. Cap Node/tRPC server memory consumption to eliminate 30GB RAM spikes.

### Key Deliverables
- PostgreSQL migration creating Materialized Views (`geo.mv_soil_field_coarse`, `geo.mv_soil_field_regional`, `geo.mv_climate_field_coarse`, etc.) with unique compound indexes for concurrent refresh.
- Updated `environmental-read-model.ts` to query pre-aggregated Materialized Views based on `resolveZoomGranularity(zoom)`.
- Targeted verification script testing spatial view queries.
