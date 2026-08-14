# Implementation Plan: Agri Data Service Scaffold

## Overview

This plan scaffolds the `agri-data-service` Python microservice in 5 phases. Each phase builds on the previous, culminating in a fully functional API with Docker Compose development environment, PostGIS spatial data models, seed data, REST endpoints, and Martin tile serving.

**Estimated total effort:** 18-24 hours across 5 phases.

---

## Phase 1: Project Initialization and Docker Compose

**Goal:** Establish the Python project structure, dependency management, dev tooling, and Docker Compose stack with PostGIS + pgvector + Redis + Martin.

### Tasks:

- [ ] Task 1.1: Create `agri-data-service/` directory with `src/agri_data_service/` package layout, `pyproject.toml` with all dependencies (FastAPI, SQLAlchemy 2.0, GeoAlchemy2, asyncpg, alembic, pydantic-settings, redis, celery, structlog), dev dependencies (pytest, pytest-asyncio, pytest-cov, httpx, ruff, mypy, factory-boy), and script entry points. (TDD: Write a test that imports the package and asserts version string exists)

- [ ] Task 1.2: Configure ruff (`ruff.toml`) with Python 3.12 target, isort rules, and 120 line length. Configure mypy (`mypy.ini`) with strict mode for `agri_data_service`. Create `Makefile` with targets: `dev`, `test`, `lint`, `format`, `migrate`, `seed`. (TDD: Run `ruff check` and `mypy` on the empty package and verify zero errors)

- [ ] Task 1.3: Create `docker-compose.yml` with services: `db` (postgis/postgis:16-3.4 with pgvector init script), `redis` (redis:7-alpine), `martin` (ghcr.io/maplibre/martin:v0.14 with PostGIS source config), `app` (Python 3.12 with uvicorn --reload, volume mount for hot reload). Add health checks on all services. Create `.env.example` with documented variables. (TDD: Write a test that parses docker-compose.yml and asserts all 4 services are defined with health checks)

- [ ] Task 1.4: Create `infra/db/init-extensions.sql` that enables `postgis`, `pgvector`, and `uuid-ossp` extensions. Create `infra/martin/config.yaml` placeholder. Verify `docker compose up -d` starts all services and they reach healthy state. (TDD: Write integration test that connects to the test database and asserts PostGIS and pgvector extensions are installed)

- [ ] Verification: Run `docker compose up -d`, confirm all services healthy, run `make test` and confirm all tests pass. [checkpoint marker]

---

## Phase 2: SQLAlchemy Models and Database Schema

**Goal:** Define all ORM models for locations, environmental profiles, strategies, species, companion relationships, and knowledge chunks with proper spatial indexes.

### Tasks:

- [ ] Task 2.1: Create `src/agri_data_service/db/engine.py` with async SQLAlchemy engine factory and async session maker. Create `src/agri_data_service/db/base.py` with declarative base including UUID PK mixin, timestamp mixin (created_at, updated_at with server defaults). (TDD: Write test that creates engine, creates a session, and executes `SELECT 1`)

- [ ] Task 2.2: Create `src/agri_data_service/models/location.py` with `Location` model: UUID PK, name, geometry (Point SRID 4326 via GeoAlchemy2), bounding_box (Polygon), usda_zone, epa_ecoregion, elevation_m, timestamps. Add GIST spatial index on geometry. (TDD: Write test that creates a Location with WKT point, saves to DB, and queries it back with ST_AsGeoJSON)

- [ ] Task 2.3: Create `src/agri_data_service/models/profiles.py` with `SoilProfile`, `ClimateProfile`, `TopographyProfile`, `WaterProfile`, `LandUseSnapshot` models. Each has UUID PK, location FK with relationship, source enum, all domain-specific columns per spec, fetched_at timestamp. (TDD: Write tests that create each profile type linked to a location and verify FK constraint, enum validation, and nullable columns)

- [ ] Task 2.4: Create `src/agri_data_service/models/strategy.py` with `Strategy` model: UUID PK, name (unique), slug (unique), category, description, all suitability range columns (min/max precip, temp, suitable_soil_types ARRAY, suitable_drainage ARRAY, max_slope_pct, min_organic_matter_pct), water_requirement enum, labor_intensity enum, time_to_yield_years, carbon_seq_potential enum, biodiversity_impact enum, created_at. (TDD: Write test that creates a strategy, verifies unique constraint on name/slug, and tests enum column validation)

- [ ] Task 2.5: Create `src/agri_data_service/models/species.py` with `Species` model (UUID PK, scientific_name unique, common_name, usda_symbol, family, growth_habit, native_status, usda_zones INT4RANGE, all tolerance/value columns, guild_roles ARRAY) and `CompanionRelationship` model (UUID PK, species_a FK, species_b FK, relationship_type enum, guild_function, notes, unique constraint on pair). (TDD: Write test that creates two species, creates a companion relationship, and verifies the unique pair constraint prevents duplicates)

- [ ] Task 2.6: Create `src/agri_data_service/models/knowledge.py` with `KnowledgeChunk` model: UUID PK, source_document, title, content text, chunk_index, embedding (Vector(1536) via pgvector), strategy FK nullable, metadata_json (JSONB), created_at. Add HNSW index on embedding column. (TDD: Write test that creates a knowledge chunk with a dummy 1536-dim vector and queries nearest neighbors using pgvector `<=>` operator)

- [ ] Task 2.7: Create `src/agri_data_service/models/__init__.py` that imports and re-exports all models. Verify all models can be imported together without circular dependencies. (TDD: Write test that imports all models and verifies `Base.metadata.tables` contains all expected table names)

- [ ] Verification: Run full test suite, verify all 10+ tables defined, all spatial and vector indexes created, all FK relationships valid. [checkpoint marker]

---

## Phase 3: Alembic Migrations and Seed Data

**Goal:** Configure Alembic for async migrations, generate the initial migration, and seed 10 strategies with realistic suitability rules.

### Tasks:

- [ ] Task 3.1: Initialize Alembic with `alembic init -t async alembic`. Configure `alembic/env.py` to import all models, use async engine, and handle GeoAlchemy2 + pgvector column types in autogenerate (register custom type comparators). Set `sqlalchemy.url` from environment variable. (TDD: Write test that runs `alembic check` and confirms no pending migrations after initial setup)

- [ ] Task 3.2: Generate initial migration with `alembic revision --autogenerate -m "initial schema"`. Verify migration includes extension creation (PostGIS, pgvector, uuid-ossp) in upgrade and proper drop in downgrade. Run `alembic upgrade head` on test database. (TDD: Write test that runs upgrade head, introspects database to verify all tables exist, then runs downgrade base and verifies tables are gone)

- [ ] Task 3.3: Create `src/agri_data_service/seed/strategies.py` with seed data for 10 strategies. Each strategy includes realistic suitability ranges researched from NRCS practice standards: Silvopasture (precip 600-1500mm, temp 5-25C, loam/clay-loam soils), Agroforestry, Hydroponics (indoor, fewer soil constraints), Aquaponics, Cover Cropping, Keyline Design (slope 2-15%), Biochar Application, Contour Farming (slope 3-20%), Managed Grazing, Permaculture Food Forest. (TDD: Write test that loads seed data into test DB and asserts 10 strategies with all suitability fields populated)

- [ ] Task 3.4: Create `src/agri_data_service/cli.py` with Click CLI commands: `seed` (runs seed script), `reset-db` (drops and recreates all tables). Register CLI as entry point in pyproject.toml. Make seed idempotent using `INSERT ... ON CONFLICT DO UPDATE`. (TDD: Write test that runs seed twice and asserts exactly 10 strategies exist with no duplicates)

- [ ] Verification: Run `alembic upgrade head` followed by `agri-data-service seed` on a fresh database. Verify 10 strategies present. Run `alembic downgrade base` and `alembic upgrade head` again to confirm clean round-trip. [checkpoint marker]

---

## Phase 4: Core API Endpoints

**Goal:** Implement REST endpoints for locations, strategies, and species with proper validation, pagination, error handling, and caching.

### Tasks:

- [ ] Task 4.1: Create `src/agri_data_service/app.py` with FastAPI app factory. Implement lifespan handler that initializes async DB engine, Redis connection pool, and disposes on shutdown. Create `src/agri_data_service/dependencies.py` with `get_db` (yields async session) and `get_redis` dependency providers. (TDD: Write test using httpx AsyncClient with app factory that hits `/health` and gets 200)

- [ ] Task 4.2: Create `src/agri_data_service/schemas/` with Pydantic v2 response/request models. Define: `LocationCreate` (lat, lng, name), `LocationResponse`, `LocationContextResponse` (nested soil/climate/topo/water/land_use), `StrategyResponse`, `StrategyListResponse`, `SpeciesResponse`, `SpeciesListResponse`, `PaginatedResponse[T]` generic, `HealthResponse`. All use `model_config = ConfigDict(from_attributes=True)`. (TDD: Write tests that validate schema serialization from ORM model instances and reject invalid input)

- [ ] Task 4.3: Create `src/agri_data_service/routers/health.py` with `GET /health` that checks DB connectivity (execute `SELECT 1`) and Redis connectivity (`PING`), returns `{"status": "ok", "db": true, "redis": true}` or appropriate degraded status. (TDD: Write tests for healthy state, DB down state, and Redis down state using dependency overrides)

- [ ] Task 4.4: Create `src/agri_data_service/routers/locations.py` with `POST /api/v1/locations` (creates location from lat/lng, returns 201 with UUID), `GET /api/v1/locations/{id}` (returns location with all profiles via eager loading, 404 if not found), `GET /api/v1/locations/{id}/context` (returns aggregated flat context object). (TDD: Write tests for create flow, get with profiles, get nonexistent returns 404, context aggregation with partial profiles)

- [ ] Task 4.5: Create `src/agri_data_service/routers/strategies.py` with `GET /api/v1/strategies` (list with optional `category` filter, pagination via limit/offset), `GET /api/v1/strategies/{id}` (detail with suitability rules, 404 if not found). Add Redis caching on list endpoint with 60s TTL. (TDD: Write tests for list, filter by category, pagination params, detail, 404, and cache hit/miss behavior)

- [ ] Task 4.6: Create `src/agri_data_service/routers/species.py` with `GET /api/v1/species` (list with filters: zone, growth_habit, nitrogen_fixer; pagination), `GET /api/v1/species/{id}` (detail with companion relationships). (TDD: Write tests for list, each filter parameter, combined filters, pagination, detail with companions, 404)

- [ ] Task 4.7: Wire all routers into app factory with `/api/v1` prefix. Add CORS middleware allowing PlantGeo origin (`http://localhost:3000`). Add request ID middleware using `uuid4` and structlog context binding. Add rate limiting middleware (slowapi, 100/min per IP). (TDD: Write tests verifying CORS headers, request ID in response headers, and rate limit response on 101st request)

- [ ] Verification: Run full test suite. Manually test all endpoints with curl against running Docker Compose stack. Verify Swagger UI at `/docs`. [checkpoint marker]

---

## Phase 5: Martin Tile Server Configuration

**Goal:** Configure Martin to serve MVT tiles from PostGIS spatial tables for map layer integration with PlantGeo.

### Tasks:

- [ ] Task 5.1: Create `infra/martin/config.yaml` with PostGIS function sources for: `locations_tiles` (point layer from locations table, zoom 2-18), `soil_layer` (locations JOIN soil_profiles with key attributes, zoom 6-16), `climate_layer` (locations JOIN climate_profiles, zoom 4-14), `water_layer` (locations JOIN water_profiles, zoom 6-16). Each source defines `id_column`, `geometry_column`, `srid`, and selected attribute columns. (TDD: Write test that parses the Martin config YAML and validates all required sources are defined with proper structure)

- [ ] Task 5.2: Create SQL function sources in a new Alembic migration for Martin. Each function takes `z`, `x`, `y`, `query_params` and returns MVT bytes using `ST_AsMVT` and `ST_AsMVTGeom`. Functions filter by tile bounding box using `ST_TileEnvelope`. (TDD: Write integration test that calls each SQL function directly and verifies it returns bytea output)

- [ ] Task 5.3: Update `docker-compose.yml` Martin service to use the config file. Verify Martin starts, discovers all sources, and responds to tile requests. Create a simple HTML test page (`infra/martin/test-viewer.html`) that loads MapLibre GL JS and renders tiles from Martin. (TDD: Write integration test that requests a tile from each Martin source endpoint and verifies 200 status with non-empty MVT response)

- [ ] Verification: Start full Docker Compose stack. Seed database with test locations. Open test viewer and visually confirm tile rendering. Verify Martin catalog endpoint lists all sources. [checkpoint marker]

---

## Dependencies Between Phases

```
Phase 1 (Project Init)
  └─> Phase 2 (Models)
        └─> Phase 3 (Migrations + Seed)
              └─> Phase 4 (API Endpoints)
              └─> Phase 5 (Martin Tiles)
```

Phases 4 and 5 can be worked in parallel after Phase 3 is complete.
