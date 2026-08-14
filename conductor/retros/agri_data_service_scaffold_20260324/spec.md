# Specification: Agri Data Service Scaffold

## Overview

Scaffold a standalone Python microservice (`agri-data-service`) that serves as the regenerative agriculture data warehouse and API layer for PlantGeo. This service exposes environmental context data, regenerative strategy definitions, and species information via a Sanic REST API, with PostGIS spatial queries served through Martin tile server for map integration.

## Background

PlantGeo is an open-source 3D geospatial mapping platform. To support regenerative agriculture use cases, we need a dedicated Python data service that stores and serves environmental profiles (soil, climate, topography, water, land use), regenerative strategy definitions, species catalogs, and knowledge base documents. This track focuses exclusively on scaffolding the repo, defining the data model, seeding initial strategy data, and standing up the core CRUD API.

The service will be consumed by PlantGeo's Next.js frontend via REST and by Martin tile server for spatial layer rendering.

## Functional Requirements

### FR-1: Python Project Initialization
**Description:** Initialize a Python 3.12 project with pyproject.toml, uv for dependency management, and development tooling.
**Acceptance Criteria:**
- pyproject.toml defines project metadata, dependencies (sanic, sanic-ext, SQLAlchemy 2.0, GeoAlchemy2, asyncpg, alembic, pydantic v2, redis[hiredis], celery), and dev dependencies (pytest, pytest-asyncio, pytest-sanic, httpx, ruff, mypy)
- **uv** used for package/dependency management (10-100x faster than pip); `uv.lock` committed to repo
- Project uses `src/agri_data_service/` layout
- Ruff configured for linting/formatting
- mypy configured with strict mode for the package
- Makefile or justfile with common commands (dev, test, lint, migrate)
**Priority:** P0

### FR-2: Sanic Application Skeleton
**Description:** Create the Sanic application with blueprint structure, middleware, health check, and OpenAPI docs.
**Acceptance Criteria:**
- Sanic app factory (`create_app()` returning `Sanic("agri-data-service")`) with `sanic-ext` `Extend` for OpenAPI, validation, and dependency injection
- `@app.before_server_start` listener initializes DB connection pool and Redis connection; `@app.after_server_stop` listener tears them down
- Blueprint modules: `locations_bp`, `strategies_bp`, `species_bp`, `health_bp` — registered via `app.blueprint()` with `/api/v1` url prefix
- CORS configured via sanic-ext CORS configuration (allowed origins for PlantGeo)
- `/health` endpoint (`@health_bp.get("/health")`) returns `sanic.response.json({"status": "ok", "db": true, "redis": true})` with actual connectivity checks
- `/docs` serves OpenAPI/Swagger UI via sanic-ext (auto-generated from Pydantic models)
- Structured JSON logging (structlog) with `@app.middleware("request")` for request ID injection and propagation
- Pydantic settings class loading from environment variables with `.env` file support
- Run via `sanic agri_data_service.app:create_app --factory` (or `sanic agri_data_service.app:create_app --factory --dev` for development)
**Priority:** P0

### FR-3: Docker Compose Development Environment
**Description:** Docker Compose stack for local development with all required infrastructure services.
**Acceptance Criteria:**
- PostgreSQL 16 with PostGIS 3.4 and pgvector extensions enabled via init script
- Redis 7 service
- Martin v1.4 tile server configured to read from PostGIS
- Application service with hot reload (volume mount + `sanic agri_data_service.app:create_app --factory --dev` for auto-reload)
- Named volumes for data persistence across restarts
- `.env.example` with all required environment variables documented
- Services use a shared Docker network
- Health checks on all services
**Priority:** P0

### FR-4: SQLAlchemy Data Models
**Description:** Define all SQLAlchemy ORM models for the regenerative agriculture data warehouse.
**Acceptance Criteria:**
- `locations` table: UUID PK, name, geometry (Point, SRID 4326), bounding_box (Polygon), usda_zone, epa_ecoregion, elevation_m, created_at, updated_at
- `soil_profiles` table: UUID PK, location FK, source (enum: ssurgo/soilgrids), soil_type, texture_class, ph, organic_matter_pct, cec, bulk_density, drainage_class, depth_cm, sand_pct, silt_pct, clay_pct, available_water_capacity, fetched_at
- `climate_profiles` table: UUID PK, location FK, source (enum: prism/noaa/nasa_power), annual_precip_mm, growing_season_days, avg_temp_c, min_temp_c, max_temp_c, frost_free_days, koppen_zone, aridity_index, monthly_precip_json, monthly_temp_json, fetched_at
- `topography_profiles` table: UUID PK, location FK, elevation_m, slope_pct, aspect_deg, curvature, twi (topographic wetness index), fetched_at
- `water_profiles` table: UUID PK, location FK, source (enum: usgs_nwis/noaa_atlas14), nearest_stream_distance_m, watershed_huc12, annual_runoff_mm, flood_zone, groundwater_depth_m, water_table_seasonal_json, fetched_at
- `land_use_snapshots` table: UUID PK, location FK, source (enum: cdl/nlcd), year, classification, crop_history_json, fetched_at
- `strategies` table: UUID PK, name (unique), slug (unique), category, description, min_precip_mm, max_precip_mm, min_temp_c, max_temp_c, suitable_soil_types (ARRAY), suitable_drainage (ARRAY), max_slope_pct, min_organic_matter_pct, water_requirement (enum: low/medium/high), labor_intensity (enum: low/medium/high), time_to_yield_years, carbon_seq_potential (enum: low/medium/high/very_high), biodiversity_impact (enum: low/medium/high), created_at
- `species` table: UUID PK, scientific_name (unique), common_name, usda_symbol, family, growth_habit, native_status, usda_zones (INT4RANGE), min_precip_mm, max_precip_mm, min_ph, max_ph, light_requirement, drought_tolerance, salt_tolerance, nitrogen_fixer (bool), pollinator_value (enum), edible (bool), timber_value (bool), guild_roles (ARRAY), created_at
- `companion_relationships` table: UUID PK, species_a FK, species_b FK, relationship_type (enum: companion/antagonist/neutral), guild_function, notes, unique constraint on (species_a, species_b)
- `knowledge_chunks` table: UUID PK, source_document, title, content (text), chunk_index, embedding (vector(1536)), strategy FK (nullable), metadata_json, created_at
- All geometry columns have spatial indexes (GIST)
- All vector columns have HNSW indexes
- All foreign keys have btree indexes
- created_at and updated_at have server defaults
**Priority:** P0

### FR-5: Alembic Migrations
**Description:** Configure Alembic for database migrations with an initial migration creating all tables.
**Acceptance Criteria:**
- Alembic configured with async SQLAlchemy engine
- `alembic/env.py` imports all models and uses GeoAlchemy2 and pgvector column types
- Initial migration creates all tables defined in FR-4
- Migration enables PostGIS and pgvector extensions before creating tables
- `alembic upgrade head` runs cleanly on a fresh database
- `alembic downgrade base` cleanly drops all tables
**Priority:** P0

### FR-6: Strategy Seed Data
**Description:** Seed the database with 10 regenerative agriculture strategies and their suitability rules.
**Acceptance Criteria:**
- Seed script (or Alembic data migration) populates the `strategies` table with: Silvopasture, Agroforestry, Hydroponics, Aquaponics, Cover Cropping, Keyline Design, Biochar Application, Contour Farming, Managed Grazing, Permaculture Food Forest
- Each strategy has realistic suitability ranges for precipitation, temperature, soil types, drainage, slope, and organic matter
- Seed script is idempotent (can be run multiple times without duplicates)
- A pytest fixture provides test strategies for unit tests
**Priority:** P1

### FR-7: Core API Endpoints
**Description:** Implement CRUD and query endpoints for locations, strategies, and species.
**Acceptance Criteria:**
- `POST /api/v1/locations` creates a location from lat/lng, returns UUID
- `GET /api/v1/locations/{id}` returns location with all associated profiles (soil, climate, topography, water, land use)
- `GET /api/v1/locations/{id}/context` returns aggregated environmental context as a flat JSON object
- `GET /api/v1/strategies` lists all strategies with optional filtering by category
- `GET /api/v1/strategies/{id}` returns strategy detail with suitability rules
- `GET /api/v1/species` lists species with pagination (limit/offset), filtering by zone, growth_habit, nitrogen_fixer
- `GET /api/v1/species/{id}` returns species detail with companion relationships
- All endpoints return proper HTTP status codes (201, 200, 404, 422)
- All list endpoints support pagination with `limit`, `offset`, `total` in response
- Response schemas defined as Pydantic v2 models (sanic-ext validates and serializes natively)
- Dependency injection for DB sessions and Redis connections via sanic-ext `@inject` decorator
- Routes use `@bp.get("/path")` / `@bp.post("/path")` blueprint pattern
**Priority:** P1

### FR-8: Martin Tile Server Configuration
**Description:** Configure Martin to serve spatial layers from PostGIS tables.
**Acceptance Criteria:**
- Martin config YAML defines tile sources for: locations (point layer), soil_profiles (joined with locations for choropleth), climate_profiles (joined with locations), water_profiles (joined with locations)
- Each tile source has appropriate min/max zoom levels
- Martin accessible at `http://localhost:3001` in Docker Compose
- Tile endpoints return valid MVT (Mapbox Vector Tiles)
- A simple test page or curl command can verify tile delivery
**Priority:** P2

## Non-Functional Requirements

### NFR-1: Performance
- API response time < 200ms for single-resource GET endpoints
- API response time < 500ms for aggregated context endpoint
- Database connection pooling with 10 min / 20 max connections
- Redis caching for strategy and species list queries (60s TTL)

### NFR-2: Testing
- pytest with pytest-asyncio for async test support
- pytest-sanic for Sanic test client in unit/integration tests
- httpx for async integration tests
- Test database created/destroyed per test session (not per test)
- Factory Boy or manual factories for test data
- Target: >80% code coverage on all modules

### NFR-3: Security
- Input validation via Pydantic on all endpoints (sanic-ext validates request bodies/params automatically)
- SQL injection prevention via SQLAlchemy parameterized queries
- Rate limiting via Sanic built-in or sanic-limiter (100 req/min per IP)
- No secrets in code; all configuration via environment variables

### NFR-4: Observability
- Structured JSON logging (structlog)
- Request ID in all log entries
- Startup banner logging service versions and configuration

## User Stories

### US-1: Developer Sets Up Local Environment
**As** a developer, **I want** to run `docker compose up` and have the full stack running, **so that** I can start developing immediately.
**Given** the repo is cloned and Docker is installed
**When** I run `docker compose up -d`
**Then** PostgreSQL (with PostGIS + pgvector), Redis, Martin, and the app are running and healthy within 60 seconds

### US-2: PlantGeo Frontend Queries Location Context
**As** the PlantGeo frontend, **I want** to create a location and retrieve its environmental context, **so that** I can display soil, climate, and water data on the map.
**Given** a location has been created at coordinates (47.6062, -122.3321)
**When** I call `GET /api/v1/locations/{id}/context`
**Then** I receive a JSON object with soil, climate, topography, water, and land use data (or nulls for unfetched profiles)

### US-3: Map Layer Rendering via Martin
**As** the PlantGeo map component, **I want** to consume vector tiles of location and profile data, **so that** I can render spatial layers with data-driven styling.
**Given** locations with soil profiles exist in the database
**When** MapLibre requests tiles from Martin at `/soil_profiles/{z}/{x}/{y}`
**Then** valid MVT tiles are returned containing geometry and attribute data

## Technical Considerations

- Use async SQLAlchemy (asyncpg driver) throughout for non-blocking I/O
- GeoAlchemy2 for spatial column types and spatial queries
- pgvector extension for embedding storage (knowledge_chunks table)
- Pydantic v2 with `model_config = ConfigDict(from_attributes=True)` for ORM serialization
- sanic-ext dependency injection (`@inject`) for database sessions and Redis connections — register dependencies via `app.ext.add_dependency()`
- Sanic blueprints for route modularity (replace FastAPI's APIRouter pattern)
- Sanic listeners (`@app.before_server_start`, `@app.after_server_stop`) for async resource lifecycle (connection pools, Redis)
- Sanic middleware (`@app.middleware("request")` / `@app.middleware("response")`) for cross-cutting concerns (request ID, logging, timing)
- sanic-ext auto-generates OpenAPI docs from Pydantic models at `/docs`
- Alembic must handle GeoAlchemy2 and pgvector column types in autogenerate

## Out of Scope

- Data ingestion workers (Track 2: data_ingestion_pipeline_20260324)
- RAG pipeline and recommendation engine (Track 3: rag_recommendation_engine_20260324)
- Authentication and authorization (future track)
- Production deployment configuration (future track)
- PlantGeo frontend integration code (separate PlantGeo track)

## Open Questions

1. Should the agri-data-service repo live inside the PlantGeo monorepo or as a truly separate repository? **Decision: monorepo under `services/` directory.** The agri-data-service lives at `services/agri-data-service/` within the PlantGeo monorepo, sharing Docker Compose infrastructure and CI pipelines while maintaining its own pyproject.toml and independent deployment.
2. Should we use Alembic data migrations or a separate seed script for strategy data? (Leaning toward a seed CLI command for flexibility)
3. What embedding model dimension should we target for knowledge_chunks? (1536 for OpenAI ada-002 compatibility, can be changed later)
