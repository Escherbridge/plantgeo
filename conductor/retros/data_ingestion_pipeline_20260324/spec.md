# Specification: Data Ingestion Pipeline

## Overview

Build a Celery-based data ingestion pipeline that populates the agri-data-service warehouse from authoritative geospatial data sources. Workers fetch soil data from SSURGO and SoilGrids, climate data from PRISM and NOAA, water data from USGS NWIS, and species data from USDA PLANTS and GBIF. A data quality layer normalizes units, handles nulls, and assigns confidence scores.

## Background

The agri-data-service (Track 1) defines the schema for environmental profiles (soil, climate, topography, water, land use) and species data, but the tables start empty. This track builds the automated ingestion workers that populate those tables from real federal and open data sources. Each worker is a Celery task that can run on-demand (triggered when a new location is created) or on a schedule (periodic refresh). The pipeline must handle API rate limits, transient failures, and data quality issues gracefully.

## Functional Requirements

### FR-1: Celery + Redis Infrastructure
**Description:** Configure Celery with Redis as broker and result backend, with beat scheduler for periodic tasks.
**Acceptance Criteria:**
- Celery app configured in `src/agri_data_service/workers/celery_app.py`
- Redis used as both broker and result backend
- Beat scheduler configured for periodic refresh tasks (daily soil refresh, hourly climate refresh)
- Task routing: `ingest.*` tasks go to `ingest` queue, `quality.*` tasks go to `quality` queue
- Retry policy: 3 retries with exponential backoff (30s, 120s, 480s) for transient failures
- Dead letter queue for tasks that exceed max retries
- Task result expiry set to 24 hours
- Celery Flower dashboard accessible for monitoring
**Priority:** P0

### FR-2: SSURGO Soil Data Worker
**Description:** Ingest soil data from the USDA SSURGO Web Soil Survey REST API into `soil_profiles`.
**Acceptance Criteria:**
- Worker accepts a location UUID, retrieves lat/lng from database
- Queries SSURGO Soil Data Access API (`SDMRestApi`) for the map unit at that point
- Extracts: soil_type (map unit name), texture_class, pH, organic_matter_pct, CEC, bulk_density, drainage_class, depth_cm, sand/silt/clay percentages, available_water_capacity
- Creates or updates `soil_profiles` row with source=`ssurgo`
- Handles case where location is outside SSURGO coverage (non-CONUS) by returning a clear "no data" result
- Respects SSURGO API rate limits (no explicit limit, but uses 2s delay between requests)
- Logs all API calls with request/response metadata
**Priority:** P0

### FR-3: PRISM / NOAA Climate Data Worker
**Description:** Ingest climate normals and growing season data from PRISM and NOAA APIs into `climate_profiles`.
**Acceptance Criteria:**
- Worker accepts a location UUID
- For CONUS locations: queries PRISM 30-year normals (4km grid) for annual precipitation, temperature ranges, frost-free days
- For all locations: queries NOAA Climate Normals API as fallback/supplement
- Extracts: annual_precip_mm, growing_season_days, avg_temp_c, min_temp_c, max_temp_c, frost_free_days, koppen_zone (derived from temp/precip), aridity_index (precip/PET), monthly_precip_json, monthly_temp_json
- Monthly data stored as JSON arrays with 12 elements (Jan-Dec)
- Creates or updates `climate_profiles` row with appropriate source enum
- Falls back to NASA POWER API for locations outside PRISM coverage
**Priority:** P0

### FR-4: USGS Water Data Worker
**Description:** Ingest water resource data from USGS National Water Information System (NWIS) into `water_profiles`.
**Acceptance Criteria:**
- Worker accepts a location UUID
- Queries USGS NWIS instantaneous values API for nearest streamgage within 50km
- Queries USGS NWIS site service for groundwater wells within 25km
- Extracts: nearest_stream_distance_m, watershed_huc12 (from WBD), annual_runoff_mm, groundwater_depth_m
- Derives water_table_seasonal_json from groundwater level time series (monthly averages over available period)
- Flood zone data from FEMA NFHL API (stretch goal; nullable for now)
- Creates or updates `water_profiles` row with source=`usgs_nwis`
- Handles locations with no nearby gages gracefully (partial data OK)
**Priority:** P1

### FR-5: Species Ingestion Worker
**Description:** Ingest species data from USDA PLANTS database and GBIF occurrence API into `species` and `companion_relationships`.
**Acceptance Criteria:**
- Batch worker (not per-location): ingests a curated list of ~500 regenerative agriculture species
- Sources species attributes from USDA PLANTS database CSV download (growth habit, native status, USDA zones, tolerances)
- Enriches with GBIF occurrence data for geographic range validation
- Populates `species` table with all columns defined in schema
- Populates `companion_relationships` from a curated companion planting dataset (CSV or JSON)
- Worker is idempotent: re-running updates existing records via `ON CONFLICT DO UPDATE`
- Logs count of inserted, updated, and skipped records
**Priority:** P1

### FR-6: SoilGrids Global Fallback Worker
**Description:** Query ISRIC SoilGrids REST API for global soil data as fallback when SSURGO is unavailable.
**Acceptance Criteria:**
- Worker accepts a location UUID, checks if `soil_profiles` already has a SSURGO record
- If no SSURGO data: queries SoilGrids REST API (`/properties/query`) at that point
- Extracts: texture class (from sand/silt/clay fractions), pH (H2O), organic carbon (converted to organic matter), bulk density, CEC
- Maps SoilGrids depth layers (0-5, 5-15, 15-30, 30-60cm) to a weighted average or selects 0-30cm topsoil
- Creates `soil_profiles` row with source=`soilgrids`
- Respects SoilGrids rate limit (100 requests/minute) using a Celery rate_limit
- Handles API downtime with retry policy
**Priority:** P1

### FR-7: Data Quality Layer
**Description:** Post-processing pipeline that normalizes units, handles null values, and assigns confidence scores to ingested data.
**Acceptance Criteria:**
- Quality task runs automatically after any ingestion task completes (Celery chain)
- Unit normalization: all temperatures to Celsius, all precipitation to mm, all depths to meters, all percentages as 0-100 floats
- Null handling: replaces sentinel values (-9999, -999, 9999) with NULL
- Confidence scoring: each profile gets a `confidence_score` (0.0-1.0) based on: data freshness (penalty for >1yr old), source reliability (SSURGO > SoilGrids), completeness (ratio of non-null fields to total fields)
- Cross-validation: flags obvious outliers (pH > 14, precip < 0, temperature > 60C)
- Quality report logged per location: fields populated, fields null, confidence score, any flags
**Priority:** P1

## Non-Functional Requirements

### NFR-1: Reliability
- All workers must be idempotent (safe to retry)
- Transient HTTP failures retried with exponential backoff
- Permanent failures (404, invalid location) marked as failed without retry
- Worker state tracked: pending, running, success, failed, no_data
- No data loss on worker crash (tasks are acknowledged only after DB commit)

### NFR-2: Performance
- Location ingestion (all workers for one location) completes within 60 seconds
- Species batch ingestion completes within 30 minutes for 500 species
- Workers use connection pooling for both database and HTTP clients
- HTTP client uses `httpx` with connection reuse and timeouts (connect=10s, read=30s)

### NFR-3: Observability
- Each worker logs: task_id, location_id, source, duration_ms, records_created, records_updated, errors
- Celery Flower dashboard for task monitoring
- Failed tasks include full error traceback in result backend
- Metrics: task success/failure counts, average duration, queue depth

### NFR-4: Rate Limiting
- Per-source rate limits enforced via Celery `rate_limit` parameter
- SSURGO: 30 requests/minute
- SoilGrids: 100 requests/minute
- USGS NWIS: 60 requests/minute
- PRISM: no explicit limit, use 1 request/second
- GBIF: 10 requests/second

## User Stories

### US-1: New Location Data Hydration
**As** the agri-data-service API, **I want** to trigger data ingestion when a new location is created, **so that** the location's environmental profiles are populated automatically.
**Given** a new location is created via `POST /api/v1/locations`
**When** the location is saved to the database
**Then** Celery tasks are dispatched for SSURGO, PRISM/NOAA, USGS, and SoilGrids (fallback) workers, and within 60 seconds the location has populated soil, climate, and water profiles

### US-2: Periodic Data Refresh
**As** a data administrator, **I want** climate and water data to refresh periodically, **so that** the platform reflects current conditions.
**Given** the Celery beat scheduler is running
**When** the daily refresh window arrives
**Then** climate and water workers re-fetch data for all locations with profiles older than 7 days

### US-3: Species Catalog Population
**As** a data administrator, **I want** to run a species ingestion job, **so that** the species catalog is populated from USDA PLANTS and GBIF.
**Given** the species worker is invoked via CLI or Celery task
**When** the worker completes
**Then** approximately 500 species records exist with growth habits, zone ranges, and tolerances populated, and companion relationships are established

### US-4: Data Quality Assurance
**As** a developer, **I want** ingested data to be validated and scored, **so that** downstream consumers can trust the data quality.
**Given** a soil profile has been ingested from SSURGO
**When** the quality task runs
**Then** units are normalized, sentinel values replaced with NULL, and a confidence score between 0.0 and 1.0 is assigned

## Technical Considerations

- All HTTP clients should use `httpx.AsyncClient` with connection pooling and proper timeout configuration
- SSURGO API returns SOAP-like XML; use an XML parser (lxml) or the simpler REST/JSON endpoints where available
- PRISM data may require downloading GeoTIFF rasters and extracting point values with `rasterio`; evaluate if the PRISM API provides point queries first
- USDA PLANTS database is available as a bulk CSV download; prefer batch ingestion over per-species API calls
- Celery tasks should use `bind=True` for access to `self.retry()` with custom countdown
- Consider using Celery `chord` or `chain` to orchestrate the full ingestion pipeline for a location: (SSURGO | PRISM | USGS) >> SoilGrids fallback >> quality check
- Store raw API responses in a `raw_responses` table or S3-compatible storage for debugging and reprocessing

## Out of Scope

- Real-time streaming data ingestion (future track)
- Satellite imagery / NDVI processing (covered by map layer track)
- Historical time-series ingestion (only current/recent data)
- FEMA flood zone ingestion (stretch goal, not required)
- NASA POWER API integration (stretch goal fallback for non-CONUS)

## Open Questions

1. Should raw API responses be stored for audit/reprocessing, or is the transformed data sufficient? (Leaning toward storing raw responses in a JSONB column on each profile)
2. For PRISM data, should we download and cache the GeoTIFF rasters locally, or rely solely on point-query APIs? (Need to evaluate PRISM API capabilities)
3. What is the curated list of ~500 regenerative species? Should we start with a smaller pilot list (~50) for initial development? (Start with 50, expand later)
4. Should the Celery workers run in the same Docker container as the API, or in separate containers? (Separate containers for independent scaling)
