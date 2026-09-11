---
type: work-registry
updated_on: 2026-09-11
---

# Current Work Registry

Per [README.md](README.md), this is the sole current work registry. Statuses are
`active`, `planned`, `blocked`, `complete` and `historical`. Every current track
directory is indexed below and its metadata uses the same current status.

The September 11 audit reconciles repository head `fa20223` with dated operational
receipts. It performed no fresh production measurement. Completed implementation,
published data, deployment and scheduled burn-in remain separate claims. The
[current runbook](RUNBOOK.md) routes operational work; the
[release policy](release-governance.md) governs release decisions.

## Active and incomplete tracks

| Track | Status | Remaining work and next gate |
| --- | --- | --- |
| [parquet_reader_cutover_acceptance_20260901](tracks/parquet_reader_cutover_acceptance_20260901/plan.md) | active | Reader hard cuts and availability contracts are implemented. Finish current product/day/zoom cold/warm request traces, explicit coverage/source-ceiling presentation, and the exact acceptance handoff. Bounded MTBS acceptance does not close all readers. |
| [gapless_parquet_publication_20260901](tracks/gapless_parquet_publication_20260901/plan.md) | active | Finish source-direct historical gap work, receipt-backed absences, effective executor ownership/cutoff and retry/restart/lease recovery. Prove three scheduled advances per activated product. Temperature history and MTBS publication are completed subsets. |
| [multiscale_polygon_surface_20260901](tracks/multiscale_polygon_surface_20260901/plan.md) | active | Renderer/support implementation is present. Complete cross-product rung conservation, pixel continuity, feature/request-to-paint budgets and desktop/mobile evidence; submit the renderer verdict. |
| [parquet_production_acceptance_20260901](tracks/parquet_production_acceptance_20260901/plan.md) | blocked | Waiting for the complete reader/renderer/writer packets. Owns full current private/public route, cold/warm browser and spatial matrices, three-schedule burn-in, exact deployed-tree validation and release/rollback verdict. |
| [repository_conformity_hardening_20260901](tracks/repository_conformity_hardening_20260901/plan.md) | active | Executable standards and multiple proven removals have landed. Finish CLI/domain and canonical-core ownership, remaining dead-code/dependency proof packets, and final integrated conformity review. Historical migrations remain evidence. |
| [parquet_duckdb_pivot_20260823](tracks/parquet_duckdb_pivot_20260823/spec.md) | active | Historical construction and private API slices are archived. Track the delegated reader, direct-writer and acceptance handoffs plus remaining product/static-lane scope. September 9 rebuild supersedes the old “PostgreSQL intact” premise. |
| [postgres_shrink_ingest_repoint_20260825](tracks/postgres_shrink_ingest_repoint_20260825/plan.md) | active | CLI/package split and baseline/rebuild slices are complete. Reconcile remaining package/removal work with current proof; source/writer work belongs to gapless and all remaining environmental retirement belongs to its successor. Do not restart the historical bridge/drain plan. |
| [environmental_postgres_retirement_20260904](tracks/environmental_postgres_retirement_20260904/plan.md) | active | Admit prepared signal/sensor repairs; restore static soil/soil-survey under their distinct contracts; recover older MTBS; replace environmental database archive paths; verify effective cutoff and remaining removal proofs. Temperature history and the 747-fire current MTBS rollout are archived as complete slices. |
| [offline_export_service_20260908](tracks/offline_export_service_20260908/plan.md) | active | Reconcile implemented builders and published temperature history with the original phase-review/performance ledger. Finish selected-builder acceptance and deferred relative-humidity 1981–2017 availability history; retain indexed-history verification before catalogue graduation. |
| [observability_log_capture_20260903](tracks/observability_log_capture_20260903/plan.md) | blocked | Six charter decisions remain: bucket provider, operator auth, tail cap, keepalive, retention and platform-tap scope. Then refresh ownership and implement bounded durable capture plus the gated operator panel. |
| [regional_fire_risk_surface_20260824](tracks/regional_fire_risk_surface_20260824/spec.md) | planned | Define the Parquet risk product and refusal outside its supported stratum; obtain held-out skill/calibration before operational claims. Historical AUC 0.725 was in-sample, only about 0.03 above VPD alone, and fell to 0.586 in closed forest. |
| [rangeland_carbon_lane_20260824](tracks/rangeland_carbon_lane_20260824/spec.md) | planned | Build a source-watermarked static SOC/OCD Parquet lane and serving/tool contract. Revalidate the historical raster inventory after the rebuild. Prior fire analysis found no carbon-targeting signal. |
| [fire_feature_plane_validation_20260824](tracks/fire_feature_plane_validation_20260824/spec.md) | planned | Fire history reconciled August 27: 9,428 calendar days, 8,359 data days, 1,069 governed absences and 3,039,749 detections. Next: held-out-season validation, calibration, coverage/cap weighting and MODIS/VIIRS normalization. Do not restart that completed archive task. |
| [fire_risk_zone_forecast_20260823](tracks/fire_risk_zone_forecast_20260823/spec.md) | planned | Feature/label/evaluation work may proceed; implement time-honest 1–2-week occurrence forecasts and honest rendering. Model training remains gated by the ML runtime/freeze decision and held-out validation. The old fire-history-hole blocker is superseded. |
| [ml_mojo_conversion_20260823](tracks/ml_mojo_conversion_20260823/spec.md) | blocked | Standing owner ML freeze/runtime decision remains open. Inventory port/retire/stays-Python modules and record the runtime/training disposition before conversion; data work is independent of this gate. |
| [rangeland_partnership_outreach_20260824](tracks/rangeland_partnership_outreach_20260824/spec.md) | planned | Refresh primary-source programmes, contacts, deadlines and land/feedstock constraints, then select authorized outreach. The August 24 contact/deadline inventory is historical; this audit sent no messages. |
| [cds_only_products_20260808](tracks/cds_only_products_20260808/plan.md) | planned | Rescope AgERA5/CEMS source scaffolding to governed Parquet and executor ownership; wire the commands, tests and bounded backfill/refresh contracts. No completed backfill is recorded. |
| [community_engagement_completion_20260805](tracks/community_engagement_completion_20260805/plan.md) | planned | Re-audit and finish the approved-to-published moderation workflow. Sensors, evacuation zones and review-outcome slices were recorded shipped; ML label bridge retains four owner questions. August 22 database counts are not current measurements. |
| [mycelium_cloud_seeding_spike_20260802](tracks/mycelium_cloud_seeding_spike_20260802/plan.md) | blocked | Five core spikes have verdicts. The explicit owner decision remains: close, run spike 006, or commission the named follow-up desk work. No closure is inferred from completed research alone. |
| [swr_indexeddb_dw_reconciliation_20260814](tracks/swr_indexeddb_dw_reconciliation_20260814/plan.md) | planned | Phase 1 IndexedDB SWR is shipped. Rescope phase 2 ETag/304 and revision semantics to current Parquet tRPC/availability generations; this is separate from the Martin tile service-worker cache. |
| [upstream_dataset_expansion_20260806](tracks/upstream_dataset_expansion_20260806/plan.md) | planned | August 23 authorization to ingest Open-Meteo products remains recorded. Replace historical Postgres-persist/cron assumptions with Parquet lanes, serving and executor ownership for remaining GloFAS/CAMS/ensemble scope, and resolve the et0 guard. Source scaffolding is not completed delivery. |
| [pnw_herbaria_source_admission_20260911](tracks/pnw_herbaria_source_admission_20260911/plan.md) | active | Resolve WTU/UBC rights, coordinate-withholding and archive-safety gates, then capture a bounded data-only DwCA pilot into immutable quarantine with schema and custody receipts. Images and production publication remain excluded. |
| [botanical_occurrence_parquet_lane_20260911](tracks/botanical_occurrence_parquet_lane_20260911/plan.md) | planned | Freeze a static release-set occurrence/taxonomy plane with dual snapshot/event time, versioned QC/taxonomy, uncertainty-aware spatial support, sparse exact aggregates, conditional publication and bounded reader/agent contracts. Depends on source admission. |
| [botanical_species_profile_lookup_20260911](tracks/botanical_species_profile_lookup_20260911/plan.md) | active | Admit non-Herbaria taxonomy, growth, fuel-composition and agricultural-role sources; use the existing database only as a reviewed authoring surface; publish approved per-value evidence as an immutable Parquet lookup; and wire a release-pinned species-information agent tool. A second trait source remains separately admitted enrichment. |
| [botanical_species_recommendation_validation_20260911](tracks/botanical_species_recommendation_validation_20260911/plan.md) | planned | Compose release-pinned specimen evidence, species growth profiles, environmental conditions and separate species-objective-effect evidence. Validate sampling bias, spatial/temporal transfer, component verdicts and abstention before any ranking or suitability claim reaches the API or agent. |
| [botanical_occurrence_experience_20260911](tracks/botanical_occurrence_experience_20260911/plan.md) | planned | Define specimen-occurrence detail, documented-taxon richness and collection-effort context layers plus honest filters and agent answers. No view may imply vegetation abundance, current occupancy or surveyed absence. Depends on the governed data-plane contract. |

## Completed slices and historical tracks

| Archive | Status | Retained scope |
| --- | --- | --- |
| [session_hygiene_20260911](retros/session_hygiene_20260911/README.md) | historical | Pruned session briefs, historical handoff/log material and cleanup provenance, with current pointers retained. |
| [parquet_operational_checkpoints_20260911](retros/parquet_operational_checkpoints_20260911/README.md) | historical | Completed September 8–9 baseline/rebuild, September 10 temperature-history publication and September 11 bounded MTBS rollout; original retirement plan and prior registry retained. Parent tracks remain open. |
| [parquet_cutover_completed_slices_20260910](retros/parquet_cutover_completed_slices_20260910/README.md) | historical | Completed pivot d0/d1/d3/d5, removal/test-intent proofs, and local verification evidence. |
| [agri_sdk_layering_20260805](retros/agri_sdk_layering_20260805/metadata.json) | historical | SDK phases 0–3 shipped; phases 4–8 explicitly superseded by later package-boundary decisions. Conformity owns remaining canonical-core work. |
| [soil_survey_lane_shape_20260825](retros/soil_survey_lane_shape_20260825/metadata.json) | complete | Static versioned release, bounded streaming and four-rung design resolved by `68da7af`; production low-zoom restoration remains open in retirement. |
| [Earlier completed-track index and August 22 reconciliation](retros/parquet_operational_checkpoints_20260911/registry-through-20260910.md) | historical | Preserves the complete numbered 01–31 and dated historical track index, original outcomes (including the reverted WebGPU work), and pivot audit without presenting old row counts or stub directories as current state. |

No incomplete parent track was archived by this pass. Update the registry,
metadata and current plan together when evidence clears a gate; add a dated
retrospective for the completed slice and keep the underlying receipts accessible.
