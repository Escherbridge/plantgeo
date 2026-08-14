# Implementation Plan: Data Ingestion Pipeline

## Overview

This plan builds the Celery-based data ingestion pipeline in 7 phases. Phase 1 establishes the Celery infrastructure. Phases 2-6 implement individual source workers. Phase 7 adds the data quality and normalization layer. Each worker follows the same pattern: HTTP client, response parser, ORM mapper, upsert logic.

**Estimated total effort:** 28-36 hours across 7 phases.
**Dependency:** Requires Track 1 (agri_data_service_scaffold_20260324) Phase 3 complete (models + migrations).

---

## Phase 1: Celery + Redis Infrastructure

**Goal:** Configure Celery application, task routing, retry policies, beat scheduler, and Flower monitoring.

### Tasks:

- [ ] Task 1.1: Create `src/agri_data_service/workers/celery_app.py` with Celery app factory. Configure Redis as broker (`redis://redis:6379/1`) and result backend (`redis://redis:6379/2`). Set task serializer to JSON, result expiry to 24h, task acks late to True. (TDD: Write test that creates Celery app and verifies broker/backend URLs and serializer config)

- [ ] Task 1.2: Configure task routing in `celery_app.py`: `ingest.*` tasks route to `ingest` queue, `quality.*` tasks route to `quality` queue. Define retry policy as a shared base class `RetryableTask` with `max_retries=3`, `default_retry_delay=30`, exponential backoff via `retry_backoff=True`, `retry_backoff_max=480`. (TDD: Write test that verifies task routing rules map correctly and RetryableTask has expected retry parameters)

- [ ] Task 1.3: Configure Celery Beat schedule in `src/agri_data_service/workers/beat_schedule.py`: `refresh-climate` runs daily at 02:00 UTC, `refresh-water` runs daily at 03:00 UTC, `refresh-stale-profiles` runs weekly on Sunday at 04:00 UTC. Each entry specifies the task name and queue. (TDD: Write test that loads beat schedule and verifies all 3 scheduled tasks with correct crontab expressions)

- [ ] Task 1.4: Add `celery-worker` and `celery-beat` services to `docker-compose.yml`. Worker service runs `celery -A agri_data_service.workers.celery_app worker -Q ingest,quality -c 4`. Beat service runs `celery -A ... beat`. Add `flower` service on port 5555. All services depend on `redis` and `db`. (TDD: Write test that parses docker-compose.yml and verifies celery-worker, celery-beat, and flower services are defined with correct commands)

- [ ] Task 1.5: Create `src/agri_data_service/workers/base.py` with `BaseIngestionTask(RetryableTask)` that provides: async DB session via context manager, structured logging with task_id/location_id, timing decorator that logs duration_ms, and a `dispatch_quality_check` method that chains the quality task after completion. (TDD: Write test that instantiates BaseIngestionTask mock, verifies DB session lifecycle, and confirms quality task is chained on success)

- [ ] Verification: Run `docker compose up -d` with Celery services. Verify Flower dashboard at `http://localhost:5555`. Submit a test task and confirm it appears in Flower. [checkpoint marker]

---

## Phase 2: SSURGO Soil Data Worker

**Goal:** Implement a Celery task that fetches soil data from the USDA SSURGO API and upserts into `soil_profiles`.

### Tasks:

- [ ] Task 2.1: Create `src/agri_data_service/workers/clients/ssurgo.py` with `SSURGOClient` class. Uses `httpx.AsyncClient` with base URL `https://SDMDataAccess.sc.egov.usda.gov`. Implements `get_map_unit_by_point(lat, lng)` that sends a POST request with the SDM tabular query (`SELECT ... FROM sacatalog, mapunit, component WHERE ...` using a spatial query). Parses JSON/XML response. (TDD: Write test with httpx mock that provides a sample SSURGO response and verifies parsing extracts map unit key, component data)

- [ ] Task 2.2: Implement `SSURGOClient.get_soil_properties(mukey)` that queries component and chorizon tables for: texture class, pH, organic matter, CEC, bulk density, drainage class, depth, sand/silt/clay percentages, available water capacity. Returns a `SoilData` dataclass with all fields. (TDD: Write test with mocked response containing multiple horizons, verify weighted average calculation for multi-horizon properties and correct field mapping)

- [ ] Task 2.3: Create `src/agri_data_service/workers/tasks/ingest_soil.py` with `ingest_soil_ssurgo` Celery task. Accepts `location_id` UUID. Loads location from DB, calls SSURGOClient, maps response to `SoilProfile` ORM model, upserts with `source=ssurgo`. Handles "no data" case (location outside CONUS) by logging and returning `{"status": "no_data", "reason": "outside_ssurgo_coverage"}`. (TDD: Write test with mocked SSURGOClient that verifies: successful upsert, no_data handling, retry on transient HTTP error, idempotent re-run)

- [ ] Task 2.4: Create `src/agri_data_service/workers/tasks/tests/test_ingest_soil_integration.py` with integration test that uses a real test database. Seeds a location, mocks only the HTTP layer (not DB), runs the task, and verifies the `soil_profiles` row has correct values. (TDD: Integration test verifies end-to-end flow from location to populated soil_profile)

- [ ] Verification: Run worker with a mocked SSURGO endpoint. Verify soil_profiles row created in test database. Check Flower for task success. [checkpoint marker]

---

## Phase 3: PRISM / NOAA Climate Data Worker

**Goal:** Implement a Celery task that fetches climate normals from PRISM and NOAA APIs and upserts into `climate_profiles`.

### Tasks:

- [ ] Task 3.1: Create `src/agri_data_service/workers/clients/prism.py` with `PRISMClient` class. Implements `get_climate_normals(lat, lng)` that queries PRISM web services for 30-year normals at a point. Returns `ClimateData` dataclass with: annual_precip_mm, monthly_precip (12-element list), monthly_temp_avg/min/max, frost_free_days. (TDD: Write test with mocked PRISM response, verify correct extraction of monthly arrays and annual aggregates)

- [ ] Task 3.2: Create `src/agri_data_service/workers/clients/noaa.py` with `NOAAClient` class. Implements `get_climate_normals(lat, lng, station_id=None)` that queries NOAA Climate Data Online API (`/cdo-web/api/v2/data`) for normals. Finds nearest station if station_id not provided. Returns same `ClimateData` dataclass. Requires NOAA API token via environment variable. (TDD: Write test with mocked NOAA response, verify station lookup and data extraction)

- [ ] Task 3.3: Implement Koppen climate zone derivation in `src/agri_data_service/workers/utils/climate.py`. Function `derive_koppen_zone(monthly_temp, monthly_precip)` applies the Koppen-Geiger classification algorithm. Also implement `calculate_aridity_index(annual_precip, annual_pet)` and `estimate_growing_season_days(monthly_temp, threshold=5.0)`. (TDD: Write tests with known climate data for Seattle (Csb), Miami (Cfa), Phoenix (BWh), and verify correct Koppen classification for each)

- [ ] Task 3.4: Create `src/agri_data_service/workers/tasks/ingest_climate.py` with `ingest_climate` Celery task. Accepts `location_id`. Tries PRISM first (CONUS only, check lat 24-50, lng -125 to -66), falls back to NOAA. Derives Koppen zone and aridity index. Maps to `ClimateProfile` ORM model, upserts. (TDD: Write tests for: PRISM success path, PRISM fallback to NOAA, both fail with retry, Koppen derivation integrated correctly)

- [ ] Task 3.5: Integration test for climate worker. Seeds a location in Pacific Northwest, mocks HTTP to return realistic Seattle climate data, runs task, verifies `climate_profiles` row with expected values (precip ~950mm, avg temp ~11C, Koppen Csb). (TDD: End-to-end integration test with assertions on all climate_profile fields)

- [ ] Verification: Run climate worker against mocked endpoints. Verify climate_profiles populated with realistic normals. Verify monthly JSON arrays have exactly 12 elements. [checkpoint marker]

---

## Phase 4: USGS Water Data Worker

**Goal:** Implement a Celery task that fetches water resource data from USGS NWIS API and upserts into `water_profiles`.

### Tasks:

- [ ] Task 4.1: Create `src/agri_data_service/workers/clients/usgs.py` with `USGSClient` class. Implements `find_nearest_streamgage(lat, lng, radius_km=50)` that queries NWIS site service (`/nwis/iv/?format=json&bBox=...&siteType=ST`) for stream gages. Returns nearest site with distance. (TDD: Write test with mocked NWIS site response containing 3 gages, verify nearest is selected and distance calculated correctly)

- [ ] Task 4.2: Implement `USGSClient.get_streamflow_stats(site_no)` that queries NWIS statistics service for annual runoff. Implement `USGSClient.find_groundwater_wells(lat, lng, radius_km=25)` that queries for groundwater wells and `get_water_levels(site_no)` that retrieves water level time series. (TDD: Write tests with mocked responses for streamflow stats and groundwater levels, verify correct parsing of NWIS JSON/RDB format)

- [ ] Task 4.3: Implement `USGSClient.get_watershed_huc(lat, lng)` using USGS Watershed Boundary Dataset (WBD) web service to retrieve HUC-12 code for a point. (TDD: Write test with mocked WBD response, verify HUC-12 extraction)

- [ ] Task 4.4: Create `src/agri_data_service/workers/tasks/ingest_water.py` with `ingest_water` Celery task. Accepts `location_id`. Calls USGSClient for nearest streamgage, groundwater wells, watershed HUC. Derives `water_table_seasonal_json` from monthly averages of groundwater levels. Creates `WaterProfile` with all available fields (nullable for missing data). (TDD: Write tests for: full data available, no streamgage within radius, no groundwater wells, partial data scenarios)

- [ ] Task 4.5: Integration test for water worker. Seeds a location near a known USGS gage, mocks HTTP with realistic NWIS responses, runs task, verifies `water_profiles` row with expected values. (TDD: End-to-end test with assertions on distance, HUC, and seasonal JSON structure)

- [ ] Verification: Run water worker against mocked endpoints. Verify water_profiles populated. Confirm graceful handling when no gages found within radius. [checkpoint marker]

---

## Phase 5: Species Ingestion Worker

**Goal:** Batch-ingest species data from USDA PLANTS CSV and GBIF API into `species` and `companion_relationships`.

### Tasks:

- [ ] Task 5.1: Create `data/species/pilot_species.csv` with 50 curated regenerative agriculture species (trees, shrubs, cover crops, nitrogen fixers, dynamic accumulators). Include columns: scientific_name, common_name, usda_symbol, family, growth_habit, native_status. Create `data/species/companion_pairs.csv` with ~100 known companion/antagonist relationships. (TDD: Write test that loads both CSVs and validates required columns exist and data is non-empty)

- [ ] Task 5.2: Create `src/agri_data_service/workers/clients/plants_db.py` with `PLANTSClient` class. Implements `load_species_from_csv(path)` that reads the pilot CSV. Implements `enrich_from_plants_api(usda_symbol)` that queries USDA PLANTS database characteristics for zone range, tolerances (drought, salt, shade), nitrogen fixation, pollinator value. Returns `SpeciesData` dataclass. (TDD: Write test with sample CSV data and mocked PLANTS API response, verify all fields mapped correctly)

- [ ] Task 5.3: Create `src/agri_data_service/workers/clients/gbif.py` with `GBIFClient` class. Implements `get_species_range(scientific_name)` that queries GBIF species match API then occurrence search API for geographic range. Returns min/max lat/lng bounding box and occurrence count. (TDD: Write test with mocked GBIF response, verify range extraction and occurrence count)

- [ ] Task 5.4: Create `src/agri_data_service/workers/tasks/ingest_species.py` with `ingest_species_batch` Celery task. Loads pilot CSV, enriches each species from PLANTS API and GBIF, upserts into `species` table using `ON CONFLICT (scientific_name) DO UPDATE`. Then loads companion CSV and upserts into `companion_relationships`. Logs summary: inserted, updated, skipped counts. (TDD: Write tests for: full batch success, partial enrichment failure (species still inserted with available data), idempotent re-run produces same record count)

- [ ] Task 5.5: Integration test for species worker. Runs batch ingest with mocked HTTP, verifies 50 species in DB with enriched fields, verifies companion relationships link valid species pairs, verifies idempotent second run. (TDD: End-to-end test with count assertions and relationship integrity checks)

- [ ] Verification: Run species batch worker. Verify 50 species in database. Verify companion relationships. Query species by zone filter and verify results. [checkpoint marker]

---

## Phase 6: SoilGrids Global Fallback Worker

**Goal:** Implement SoilGrids REST API client as fallback soil data source for locations outside SSURGO coverage.

### Tasks:

- [ ] Task 6.1: Create `src/agri_data_service/workers/clients/soilgrids.py` with `SoilGridsClient` class. Implements `get_soil_properties(lat, lng)` that queries `https://rest.isric.org/soilgrids/v2.0/properties/query` with parameters for: sand, silt, clay, phh2o, soc, bdod, cec. Requests depth layers 0-5cm, 5-15cm, 15-30cm. (TDD: Write test with mocked SoilGrids response, verify extraction of property values at each depth layer)

- [ ] Task 6.2: Implement `SoilGridsClient.compute_weighted_topsoil(depths_data)` that computes weighted average of 0-30cm topsoil from the three depth layers (weights proportional to layer thickness: 5/30, 10/30, 15/30). Implement texture class derivation from sand/silt/clay fractions using USDA texture triangle. Implement organic carbon to organic matter conversion (factor 1.724). (TDD: Write tests for weighted average calculation, texture triangle classification for known compositions (e.g., 40/40/20 = loam), and OC to OM conversion)

- [ ] Task 6.3: Create `src/agri_data_service/workers/tasks/ingest_soil_global.py` with `ingest_soil_soilgrids` Celery task. Accepts `location_id`. Checks if SSURGO profile already exists; if yes, skips. Otherwise queries SoilGrids, computes weighted topsoil, maps to `SoilProfile` with source=`soilgrids`. Celery `rate_limit='100/m'` enforced. (TDD: Write tests for: SSURGO exists skips, SoilGrids success path, rate limit config present, transient error retry)

- [ ] Task 6.4: Integration test for SoilGrids fallback. Seeds a location with no SSURGO profile, mocks SoilGrids HTTP, runs task, verifies soil_profiles row with source=soilgrids. Seeds another location with existing SSURGO profile, runs task, verifies it skips without API call. (TDD: End-to-end test with skip and ingest scenarios)

- [ ] Verification: Run SoilGrids worker for a non-CONUS location. Verify soil_profiles populated with source=soilgrids. Verify SSURGO locations are skipped. [checkpoint marker]

---

## Phase 7: Data Quality Layer

**Goal:** Build post-ingestion quality checks that normalize units, handle sentinel values, validate ranges, and assign confidence scores.

### Tasks:

- [ ] Task 7.1: Create `src/agri_data_service/workers/quality/normalizer.py` with unit normalization functions: `normalize_temperature(value, from_unit) -> celsius`, `normalize_precipitation(value, from_unit) -> mm`, `normalize_depth(value, from_unit) -> meters`, `normalize_percentage(value) -> float 0-100`. Handle edge cases: None passthrough, already-correct units. (TDD: Write tests for each conversion function with known values: 32F=0C, 1 inch=25.4mm, 100cm=1m, 0.5=50%)

- [ ] Task 7.2: Create `src/agri_data_service/workers/quality/validators.py` with validation functions: `replace_sentinels(value, sentinels=[-9999, -999, 9999]) -> value|None`, `validate_ph(value) -> value|None` (valid 0-14), `validate_temperature(value) -> value|None` (valid -90 to 60), `validate_precipitation(value) -> value|None` (valid 0-15000), `validate_percentage(value) -> value|None` (valid 0-100). Each returns None and logs warning for invalid values. (TDD: Write tests for each validator with valid, invalid, and sentinel inputs)

- [ ] Task 7.3: Create `src/agri_data_service/workers/quality/scorer.py` with `calculate_confidence_score(profile, source, fetched_at)` function. Score formula: `base_source_score * completeness_ratio * freshness_factor`. Source scores: SSURGO=0.95, PRISM=0.90, USGS=0.90, NOAA=0.85, SoilGrids=0.80, GBIF=0.75. Completeness = non_null_fields / total_fields. Freshness = 1.0 if <30 days, 0.9 if <90 days, 0.8 if <365 days, 0.6 if older. (TDD: Write tests for score calculation with various completeness ratios, sources, and freshness dates)

- [ ] Task 7.4: Create `src/agri_data_service/workers/tasks/quality_check.py` with `run_quality_check` Celery task. Accepts `location_id` and `profile_type`. Loads the profile, runs normalizer on all numeric fields, runs validators, runs scorer, updates the profile row with normalized values and confidence_score. Logs quality report. (TDD: Write tests for: soil profile quality check, climate profile quality check, profile with sentinel values cleaned, confidence score written to DB)

- [ ] Task 7.5: Wire quality check into ingestion pipeline. Modify each ingestion task to chain `run_quality_check` after successful upsert using `self.apply_async(link=quality_check.s(location_id, profile_type))`. (TDD: Write test that triggers an ingestion task and verifies quality_check task is dispatched as a callback)

- [ ] Task 7.6: Create orchestration task `ingest_location_full` in `src/agri_data_service/workers/tasks/orchestrate.py`. Uses Celery `chord`: runs (SSURGO | PRISM | USGS) in parallel, then conditionally runs SoilGrids fallback, then runs quality checks on all profiles. This is the task dispatched when a new location is created. (TDD: Write test that triggers full orchestration and verifies all sub-tasks are dispatched in correct order with correct dependencies)

- [ ] Verification: Create a location via API. Verify full orchestration runs: all workers dispatch, quality checks run, confidence scores assigned. Check Flower for task chain execution. [checkpoint marker]

---

## Dependencies Between Phases

```
Phase 1 (Celery Infra)
  ├─> Phase 2 (SSURGO)
  ├─> Phase 3 (PRISM/NOAA)
  ├─> Phase 4 (USGS)
  ├─> Phase 5 (Species)
  └─> Phase 6 (SoilGrids) ── depends on Phase 2 for skip logic
        └─> Phase 7 (Quality) ── depends on all workers existing
```

Phases 2, 3, 4, and 5 can be worked in parallel after Phase 1. Phase 6 requires Phase 2's SSURGO client for skip logic. Phase 7 depends on all workers being implemented.
