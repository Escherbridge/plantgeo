---
type: track-index
---

# Conductor Tracks

## [~] Track: North American Intervention Evidence Data [north_america_intervention_data_20260723]
Ingest maximum useful free/open wildfire, drought, watershed, agriculture, and
regulatory evidence with explicit native support and licence gates. Prove the
architecture with an open-data Boise city/named-property vertical slice while
keeping forecasts, strategies, recommendations, Railway, and schedules untouched.

## [~] Track: Forecast Validation and Railway Predeploy [forecasting_predeploy_20260722]
Validate a governed metric forecast against pinned local history, persist immutable
forecast-versus-actual hindcasts as future ML signals, and rehearse the future
Railway database path without deploying or mutating Railway. The first candidate
was correctly rejected; the current continuation adds a generic date-spined,
30-day Monte Carlo iteration and actual-reconciliation loop that remains
evaluation-only.

## [ ] Track: Map Layer Data Visualization [map_layer_data_viz_20260324]
Get all PlantGeo map layers rendering with real or demo data over Washington State.
Layers: Vegetation/NDVI, Water, Drought, Soil, Fire.

## [ ] Track: Agri Data Service Scaffold [agri_data_service_scaffold_20260324]
Scaffold the agri-data-service Python repo with FastAPI, SQLAlchemy, PostGIS, pgvector, and core schema. Docker Compose dev environment, 10 SQLAlchemy models, Alembic migrations, seed data for 10 regenerative strategies, core CRUD API, Martin tile server config.

## [ ] Track: Data Ingestion Pipeline [data_ingestion_pipeline_20260324]
Build Celery workers to ingest data from SSURGO, PRISM, USGS, USDA PLANTS, GBIF, and SoilGrids into the data warehouse. Includes beat scheduler, per-source workers, and a data quality layer with unit normalization and confidence scoring. Depends on: agri_data_service_scaffold_20260324.

## [ ] Track: RAG Recommendation Engine [rag_recommendation_engine_20260324]
Build the RAG pipeline and suitability scoring engine for AI-driven regenerative strategy recommendations. Knowledge base ingestion, chunking + embedding, SQL-based strategy scoring, species recommendations with companion planting, LLM synthesis endpoint, PlantGeo tRPC integration. Depends on: agri_data_service_scaffold_20260324, data_ingestion_pipeline_20260324.
