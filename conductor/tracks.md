---
type: work-registry
---

# Current Work Registry

Per [`README.md`](./README.md), this file is the sole current work registry.
Material under `retros/` contains completed tracks and retrospectives. Status vocabulary: active, planned, blocked, complete, historical.

Registry updated 2026-08-14.

## Active & Incomplete Tracks

| Track | Status | Type | Summary |
|-------|--------|------|---------|
| [agri_sdk_layering_20260805](tracks/agri_sdk_layering_20260805/) | planned | refactor | Restructure agri_data_service (36,438 lines) into six layers with a lint-enforceable dependency rule, separating pure Monte Carlo and ML method code over a shared foundation, dissolving the 2,474-line cli.py into thin command wiring, and exposing a public importable SDK surface without changing any of the 52 CLI command strings |
| [cds_only_products_20260808](tracks/cds_only_products_20260808/) | planned | feature | Backfill the three Copernicus products Open-Meteo does not redistribute -- AgERA5 agrometeorological indicators on the classic CDS host with zero new credential plumbing, CEMS fire danger indices behind a new, separately-registered EWDS host and key, and seasonal forecasts scoped as a later, lower-confidence phase -- reusing historical_era5.py's working cdsapi client, checkpointing and raw-cache shape as the integration template rather than inventing a second one |
| [community_engagement_completion_20260805](tracks/community_engagement_completion_20260805/) | active | feature | Close the community engagement loop: make expert moderation reachable so submitted interventions can reach the map, restore the sensors layer whose removal premise is now false, publish evacuation-zones end to end, return review outcomes to submitters, and decide whether community submissions become ML labels |
| [mycelium_cloud_seeding_spike_20260802](tracks/mycelium_cloud_seeding_spike_20260802/) | active | research-spike | Feasibility spikes for INA-fungal products (soil amendment, growth media, animal feed) that rely on natural spore transport for bioprecipitation |
| [swr_indexeddb_dw_reconciliation_20260814](tracks/swr_indexeddb_dw_reconciliation_20260814/) | active | feature | IndexedDB Stale-While-Revalidate (SWR) cache layer with background Data Warehouse (DW) revision reconciliation, ETag HTTP 304 validation, and 100% geospatial layer coverage |
| [upstream_dataset_expansion_20260806](tracks/upstream_dataset_expansion_20260806/) | planned | feature | Retire the CDS ERA5-Land soil lane for its keyless Open-Meteo equivalent, close the et0-model trap that would silently persist an all-NULL signal, and add four new upstream datasets -- fire-weather VPD, GloFAS river discharge, CAMS air quality, and ensemble forecast uncertainty -- through the durable-backfill and Railway-cron standards already established |
| [webworker_webgpu_acceleration_20260814](tracks/webworker_webgpu_acceleration_20260814/) | active | feature | Dedicated Web Worker data engine and WebGPU hardware acceleration manager for zero-copy buffer transfer and instant GPU-accelerated layer rendering with WebGL fallback |

## Completed Tracks & Retrospectives

| Track | Status | Type | Summary |
|-------|--------|------|---------|
| [01-core-map-engine](retros/01-core-map-engine/) | completed | feature | 01-core-map-engine |
| [02-vector-tile-pipeline](retros/02-vector-tile-pipeline/) | completed | feature | 02-vector-tile-pipeline |
| [03-deck-gl-visualization](retros/03-deck-gl-visualization/) | completed | feature | 03-deck-gl-visualization |
| [04-routing-navigation](retros/04-routing-navigation/) | completed | feature | 04-routing-navigation |
| [05-geocoding-search](retros/05-geocoding-search/) | completed | feature | 05-geocoding-search |
| [06-realtime-data-streaming](retros/06-realtime-data-streaming/) | completed | feature | 06-realtime-data-streaming |
| [07-layer-management](retros/07-layer-management/) | completed | feature | 07-layer-management |
| [08-three-js-3d-objects](retros/08-three-js-3d-objects/) | completed | feature | 08-three-js-3d-objects |
| [09-drawing-measurement](retros/09-drawing-measurement/) | completed | feature | 09-drawing-measurement |
| [10-wildfire-prevention](retros/10-wildfire-prevention/) | completed | feature | 10-wildfire-prevention |
| [11-offline-pwa](retros/11-offline-pwa/) | completed | feature | 11-offline-pwa |
| [12-auth-multitenancy](retros/12-auth-multitenancy/) | completed | feature | 12-auth-multitenancy |
| [13-analytics-dashboard](retros/13-analytics-dashboard/) | completed | feature | 13-analytics-dashboard |
| [14-fleet-tracking](retros/14-fleet-tracking/) | completed | feature | 14-fleet-tracking |
| [15-ui-design-system](retros/15-ui-design-system/) | completed | feature | 15-ui-design-system |
| [16-street-view-imagery](retros/16-street-view-imagery/) | completed | feature | 16-street-view-imagery |
| [17-places-poi](retros/17-places-poi/) | completed | feature | 17-places-poi |
| [18-railway-deployment](retros/18-railway-deployment/) | completed | feature | 18-railway-deployment |
| [19-testing-quality](retros/19-testing-quality/) | completed | feature | Testing & quality: vitest, Playwright E2E, Redis/PostGIS integration, mobile layouts, benchmarks + perf regression, pre-commit hooks; CI superseded by Dockerfile gates |
| [20-embed-api](retros/20-embed-api/) | completed | feature | 20-embed-api |
| [21-wildfire-enhancement](retros/21-wildfire-enhancement/) | completed | feature | 21-wildfire-enhancement |
| [22-water-scarcity](retros/22-water-scarcity/) | completed | feature | 22-water-scarcity |
| [23-vegetation-land-cover](retros/23-vegetation-land-cover/) | completed | feature | 23-vegetation-land-cover |
| [24-soil-health](retros/24-soil-health/) | completed | feature | 24-soil-health |
| [25-community-strategy-requests](retros/25-community-strategy-requests/) | completed | feature | 25-community-strategy-requests |
| [26-strategy-cards](retros/26-strategy-cards/) | completed | feature | 26-strategy-cards |
| [27-team-organization-pages](retros/27-team-organization-pages/) | completed | feature | 27-team-organization-pages |
| [28-plantcommerce-integration](retros/28-plantcommerce-integration/) | completed | feature | 28-plantcommerce-integration |
| [29-environmental-alerts](retros/29-environmental-alerts/) | completed | feature | 29-environmental-alerts |
| [30-environmental-analytics](retros/30-environmental-analytics/) | completed | feature | 30-environmental-analytics |
| [31-ai-regional-intelligence](retros/31-ai-regional-intelligence/) | completed | feature | 31-ai-regional-intelligence |
| [agri_data_service_scaffold_20260324](retros/agri_data_service_scaffold_20260324/) | completed | feature | agri_data_service_scaffold_20260324 |
| [ai_regional_agent_expansion_20260814](retros/ai_regional_agent_expansion_20260814/) | completed | feature | Expand Anthropic Claude 3.5 AI agent (RegionalIntelligencePanel.tsx) to synthesize ML strategy selection models, explain causal intervention trade-offs (biochar vs agroforestry vs cover crops), query live stream telemetry, and cite land evidence. |
| [community_intervention_lifecycle_20260814](retros/community_intervention_lifecycle_20260814/) | completed | feature | Consolidated track unifying community strategy proposals, expert moderation scorecards (/moderation), proposal feed (/feed), and post-intervention telemetry monitoring (USGS streamflow, soil moisture, fire risk). |
| [data_ingestion_pipeline_20260324](retros/data_ingestion_pipeline_20260324/) | completed | feature | data_ingestion_pipeline_20260324 |
| [dw_materialized_zoom_aggregation_20260814](retros/dw_materialized_zoom_aggregation_20260814/) | completed | feature | Pre-aggregate geospatial layer zoom tiers in PostGIS using Materialized Views (coarse-average, regional-average) with spatial GIST indexes and fast REFRESH routines for instant zoom serving |
| [forecasting_predeploy_20260722](retros/forecasting_predeploy_20260722/) | completed | infrastructure | Governed local metric forecasting, persisted hindcast signals, and Railway predeploy rehearsal |
| [inapp_job_runner_admin_20260814](retros/inapp_job_runner_admin_20260814/) | completed | feature | Postgres-backed internal job runner engine in agri-data-service replacing Railway crons, complete with schedule management, work-lease management, manual trigger toggles, and Platform Admin UI control panel. |
| [ingestion_warehouse_consolidation_20260803](retros/ingestion_warehouse_consolidation_20260803/) | completed | feature | Consolidate ingestion and the warehouse: cut the enforcement layer while preserving the ML serving lane, key everything to one Type-2 conformed geometry dimension, port ingestion to the Python CLI, and serve past-to-future through one day-granular read path |
| [map_layer_data_viz_20260324](retros/map_layer_data_viz_20260324/) | completed | feature | Map Layer Data Visualization - Get all map layers rendering with real or demo data over Washington State |
| [ml_recommendation_models_20260808](retros/ml_recommendation_models_20260808/) | completed | feature | Expert-label plane, k-NN analog forecasting, conformal self-correction, and two literature-grounded recommendation models (species fit, strategy selection) |
| [ml_strategy_materialized_rendering_20260814](retros/ml_strategy_materialized_rendering_20260814/) | completed | feature | Pre-aggregate ML strategy selection models and causal intervention recommendations into PostGIS Materialized Views across zoom tiers (coarse, regional, detail) with GIST spatial indexing and concurrent refresh routines for MapLibre/Martin tile serving. |
| [model_delivery_public_evaluation_20260726](retros/model_delivery_public_evaluation_20260726/) | completed | model-delivery | Orchestrated delivery of checksum-bound public crop-spectrum and meteorological forecast evaluation artifacts |
| [north_america_intervention_data_20260723](retros/north_america_intervention_data_20260723/) | completed | infrastructure | Resolution-aware North American evidence ingestion for wildfire, drought, watershed restoration, and regenerative production planning |
| [rag_recommendation_engine_20260324](retros/rag_recommendation_engine_20260324/) | completed | feature | rag_recommendation_engine_20260324 |
| [restoration_ag_demo_20260726](retros/restoration_ag_demo_20260726/) | completed | product-governance | Evaluation-only, goal-specific predictive demo for restoration-agriculture evidence and operational signals |
| [seasonal_forecast_feedback_20260726](retros/seasonal_forecast_feedback_20260726/) | completed | infrastructure | Evaluation-only seasonal forecasting, time-honest residual feedback, and ML feature lineage |
| [strategy_selection_governance_20260726](retros/strategy_selection_governance_20260726/) | completed | governance | Research-only intervention-effect label, training, and selection lineage governance |
