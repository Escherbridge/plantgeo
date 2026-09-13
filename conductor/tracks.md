---
type: track-registry
updated_on: 2026-09-12
---

# Current tracks

This registry contains planned, active, and blocked work only. Completed implementation history and retired migration plans are intentionally absent.

## Environmental data and product acceptance

| Track | State | Outstanding work |
| --- | --- | --- |
| [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/plan.md) | active | Remove the remaining environmental PostgreSQL surface; complete direct writers, governed readers, layer registration, and agent parity. |
| [Gapless Parquet publication](tracks/gapless_parquet_publication_20260901/plan.md) | active | Close full historical horizons, governed absences, repair ownership, and sustained forward publication. |
| [Multiscale polygon surfaces](tracks/multiscale_polygon_surface_20260901/plan.md) | active | Complete low-zoom support geometry, live selected-day rendering, performance, and browser evidence. |
| [Parquet production acceptance](tracks/parquet_production_acceptance_20260901/plan.md) | blocked | Run cross-layer conservation, freshness, cold/warm, schedule burn-in, and release checks after serving work is ready. |
| [Offline export service](tracks/offline_export_service_20260908/plan.md) | active | Complete the production builder, availability-cost controls, and export verification. |
| [Platform experience QA](tracks/platform_experience_qa_20260911/plan.md) | active | Run populated-data, selected-day, mobile, accessibility, cache, canvas, agent, and MCP journeys. |
| [Repository conformity hardening](tracks/repository_conformity_hardening_20260901/plan.md) | active | Remove proven dead paths and keep executable architecture boundaries aligned with current documentation. |

## Weather and fire

| Track | State | Outstanding work |
| --- | --- | --- |
| [Weather forecast Parquet lane](tracks/weather_forecast_parquet_lane_20260911/plan.md) | planned | Admit run-time and valid-time forecasts, publish direct Parquet products, and register serving and agent tools. |
| [Traditional forecast experience](tracks/weather_forecast_experience_20260911/plan.md) | planned | Render continuous weather fields, wind, and useful hourly and daily forecast cards beyond the observation horizon. |
| [Fire feature plane validation](tracks/fire_feature_plane_validation_20260824/spec.md) | planned | Validate fire features across seasons and resolutions. |
| [Regional fire-risk surface](tracks/regional_fire_risk_surface_20260824/spec.md) | planned | Build the cross-state prioritization surface after its source and validation gates are ready. |
| [Fire-risk zone forecast](tracks/fire_risk_zone_forecast_20260823/spec.md) | planned | Develop and validate the one-to-two-week fire-risk forecast. |

## Botanical and land context

| Track | State | Outstanding work |
| --- | --- | --- |
| [Botanical species profile lookup](tracks/botanical_species_profile_lookup_20260911/plan.md) | active | Census the approved Railway lookup, admit growth and composition sources, publish immutable profiles, and prove API/agent/MCP parity. |
| [PNW Herbaria source admission](tracks/pnw_herbaria_source_admission_20260911/plan.md) | active | Resolve exact release identity, field maps, reuse and coordinate policies, and quarantine controls before a bounded specimen pilot. |
| [Botanical occurrence Parquet lane](tracks/botanical_occurrence_parquet_lane_20260911/plan.md) | planned | Publish governed occurrence and taxonomy data after source admission. |
| [Botanical occurrence experience](tracks/botanical_occurrence_experience_20260911/plan.md) | planned | Add map and agent experiences after the occurrence product is accepted. |
| [Species recommendation validation](tracks/botanical_species_recommendation_validation_20260911/plan.md) | planned | Establish evidence and evaluation for species-specific recommendations. |
| [PNW land reference plane](tracks/pnw_land_context_reference_plane_20260911/plan.md) | planned | Resolve source rights and publish public boundaries, offices, advisers, and contact-process references. |
| [PNW land contact experience](tracks/pnw_land_contact_experience_20260911/plan.md) | planned | Design the public contact workflow after reference-plane admission. |

## Other planned or blocked work

| Track | State |
| --- | --- |
| [Intervention drawing + visibility](tracks/intervention_drawing_visibility_20260912/plan.md) | planned |
| [Community engagement completion](tracks/community_engagement_completion_20260805/plan.md) | planned |
| [CDS-only products](tracks/cds_only_products_20260808/plan.md) | planned |
| [Rangeland carbon lane](tracks/rangeland_carbon_lane_20260824/spec.md) | planned |
| [Rangeland partnership outreach](tracks/rangeland_partnership_outreach_20260824/spec.md) | planned |
| [SWR IndexedDB/DW reconciliation](tracks/swr_indexeddb_dw_reconciliation_20260814/plan.md) | planned |
| [Upstream dataset expansion](tracks/upstream_dataset_expansion_20260806/plan.md) | planned |
| [ML to Mojo conversion](tracks/ml_mojo_conversion_20260823/spec.md) | blocked |
| [Mycelium cloud-seeding spike](tracks/mycelium_cloud_seeding_spike_20260802/plan.md) | blocked |
| [Observability log capture](tracks/observability_log_capture_20260903/plan.md) | blocked |
