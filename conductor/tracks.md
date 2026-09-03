---
type: work-registry
---

# Current Work Registry

Per [`README.md`](./README.md), this file is the sole current work registry.
Material under `retros/` contains completed tracks and retrospectives. Status vocabulary: active, planned, blocked, complete, historical.

Registry updated 2026-09-02 (waves 1–3 through `12fa189`, pushed).

**2026-08-22 — reconciled against the architecture pivot.** `conductor/RUNBOOK.md`
§0.23 (Postgres becomes a community-features-only database; every data plane
moves to day-partitioned Parquet read by DuckDB+Polars, Martin serves generated
PMTiles) and §0.24 (the 21-stream, 5-wave execution plan, governed by
`conductor/code_styleguides/layer-lanes.md`) supersede or re-scope several
tracks below. Verdicts are evidence-based (working tree + git history), not
taken from each track's own status field, several of which were stale. Full
detail for each verdict lives in that track's own `metadata.json` under a
`pivot_reconciliation_20260822` key; this table carries the summary. See
"Pivot reconciliation notes" below for the single most load-bearing finding
(`agri_sdk_layering_20260805` vs §0.24).

## Active & Incomplete Tracks

| Track | Status | Type | Summary |
|-------|--------|------|---------|
| [parquet_reader_cutover_acceptance_20260901](tracks/parquet_reader_cutover_acceptance_20260901/) | active | feature | Hard-cut eligible live map/tRPC/capability reads to bounded day+bbox+zoom Parquet responses with explicit terminal/refusal/truncation states. Slider availability comes from a checksum-bound generational Parquet index, never request-time history listings or PostgreSQL fallback. Receives pivot slice `d4`; performs no data repair or PostgreSQL deletion. |
| [gapless_parquet_publication_20260901](tracks/gapless_parquet_publication_20260901/) | active | infrastructure | Establish one durable product/source owner for forward refresh, repair and coverage status under the stateful executor. Bootstrap each lane's availability index once, extend it transactionally with ingestion, close the observed 27/31/94-day tails, publish governed absences, prove leases/recovery and prevent legacy/executor overlap. Receives forward-publication scope from shrink `s2b-s4`; retirement remains there. |
| [multiscale_polygon_surface_20260901](tracks/multiscale_polygon_surface_20260901/) | active | feature | Add explicit support geometry and semantically correct multiscale rendering: tessellated/filled continuous fields, aggregate event cells or heatmaps, raw detail points, and topology-preserving native polygons. Fire density cells must never masquerade as fire perimeters. Begins after the reader support contract freezes. |
| [parquet_production_acceptance_20260901](tracks/parquet_production_acceptance_20260901/) | blocked | verification | Evidence-only fan-in gate for the preceding three tracks: private R2 probes, cold/warm browser matrices, rung conservation, pixel continuity, three-schedule writer burn-in, integrated sweep, independent review and exact release/rollback verdict. A GREEN result may unblock retirement review; this track performs no repair or deletion. |
| [repository_conformity_hardening_20260901](tracks/repository_conformity_hardening_20260901/) | active | refactor | Remove the fabricated moderation score first, make checked-in style rules executable, restore thin CLI and canonical snapshot/schema ownership, and remove only evidence-proven dead source/dependencies. Production reader, writer and spatial fixes remain with their existing tracks; migrations/history are protected evidence, not static-scan dead code. |
| [parquet_duckdb_pivot_20260823](tracks/parquet_duckdb_pivot_20260823/) | active | feature | Historical data construction is complete: the immutable 46,146,568-row canonical snapshot and governed climate/soil product lanes reconcile exactly. `d1` and `d5` are superseded-complete; private API/core work is integrated. `d4` is delegated to `parquet_reader_cutover_acceptance_20260901`, and the final browser verdict belongs to `parquet_production_acceptance_20260901`. PostgreSQL stays intact until the successor gates clear. |
| [postgres_shrink_ingest_repoint_20260825](tracks/postgres_shrink_ingest_repoint_20260825/) | active | feature | **Successor retirement authority.** Shared `parquet_ops/` and the CLI split/rename are integrated. Unfinished forward writer, repair and executor activation work is delegated to `gapless_parquet_publication_20260901`; P5/P6 retirement and shrink stay here and remain blocked on the new reader, publication, spatial and acceptance tracks. |
| [regional_fire_risk_surface_20260824](tracks/regional_fire_risk_surface_20260824/) | chartered | feature | **First analytical product of the drained warehouse** (RUNBOOK 0.41). Serves the rangeland fire-risk index as a **day-partitioned Parquet lane**, one day per read, per the ingestion constraint in 0.41.7. Measured leakage-free on 2026: composite **AUC 0.725**, decile lift **14.9% → 67.1% burned**. Two honesty constraints it must carry, not drop: the composite beats **VPD alone (0.697) by only ~0.03**, and the figure is **in-sample** until `fire_feature_plane_validation_20260824` clears. **STRATIFIED — skill decays to 0.586 in closed forest; the lane must refuse out-of-stratum cells rather than extrapolate.** Code already landed at `services/agri-data-service/analysis/`. |
| [rangeland_carbon_lane_20260824](tracks/rangeland_carbon_lane_20260824/) | chartered | feature | SoilGrids **SOC (g/kg) and OCD (kg/m³)** sampled per cell, already published and catalogued in `geo.published_raster`; `geo.soil_survey` carries **no** carbon field, so this is the estate's only carbon source. A **`static_lookup` lane keyed to a source watermark** — SOC does not vary by day and a `daily_series` nature would rewrite it 365×/year. Unblocked 2026-08-24: `rasterio>=1.3,<2` pinned in `pyproject.toml` and installed with **`uv pip install`, not `uv sync`** (pytest verified surviving at 9.1.1). **Must not be justified as fire targeting — RUNBOOK 0.41.4 found no carbon signal in the fire index.** |
| [fire_feature_plane_validation_20260824](tracks/fire_feature_plane_validation_20260824/) | planned | feature | Runnable: the historical archive now reconciles 9,428 calendar days, 8,359 data days, 1,069 governed absences, and 3,039,749 detections. Modeling remains outstanding: convert AUC 0.725 from an in-sample association into held-out-season skill and calibrated probability while handling coverage imbalance, upstream caps, ignition, and the MODIS-to-VIIRS sensor change. Do not restart the completed fire data task. |
| [rangeland_partnership_outreach_20260824](tracks/rangeland_partnership_outreach_20260824/) | chartered | non-software | Programme, partnership and feedstock-policy lane; ships no code and owns no source files (partitions deliberately empty, `confidence: none`). Records the technique verdicts (**silvopasture poor fit, agroforestry largely inverted, biochar strong fit, vegetation management already core**), the **nitrogen tension** (manure favours cheatgrass; biochar is the opposite lever), the greenstrip convergence with 0.41.3's fuel-continuity threshold, land tenure (**BLM = 61% of Harney County**), and live doors with real deadlines (**OWEB Summer 2026 closed 2026-08-03**; WSRRI funded 2025–27; Idaho Sage Grouse Action Team). **Only Harney SWCD contact details are primary-source verified; no staff names, phones or emails were invented.** |
| [cds_only_products_20260808](tracks/cds_only_products_20260808/) | planned | feature | Backfill AgERA5 and CEMS from Copernicus CDS/EWDS. Still valid, not superseded (new upstream sources are independent of the storage-backend pivot). Contract-class scaffolding exists (`historical_agera5.py`, `historical_cems.py`) but is unwired -- no CLI verbs, no tests, no backfill run. Needs light rescoping so its eventual persistence target is a Parquet lane, not a Postgres warehouse row. |
| [community_engagement_completion_20260805](tracks/community_engagement_completion_20260805/) | active | feature | Close the community engagement loop. **Not superseded -- more canonical post-pivot**, since §0.23.4 retains Postgres for community features only. Sensors (phase 2), evacuation-zones (phase 3) and review-outcome surfacing (phase 4) are all confirmed **done**, mostly via the already-archived `community_intervention_lifecycle_20260814`. Phase 1 (moderation) is partial: the UI/gating path works, but `interventions` sits at 2 rows, both `status='approved'`, 0 published -- the publish step is never invoked. Phase 5 (ML label bridge) stays blocked on the owner's four open questions. |
| [mycelium_cloud_seeding_spike_20260802](tracks/mycelium_cloud_seeding_spike_20260802/) | blocked | research-spike | Feasibility spikes for INA-fungal products. Orthogonal to the pivot. All 5 core spikes already returned verdicts (PARTIAL/VALIDATED) and `spikes/README.md` records a track-level conclusion, but the track's own plan requires an explicit owner call among three next steps (close / run spike 006 / commission follow-up desk items) that has not been made. Left `blocked`, not archived -- see notes below. |
| [swr_indexeddb_dw_reconciliation_20260814](tracks/swr_indexeddb_dw_reconciliation_20260814/) | active | feature | IndexedDB SWR cache for tRPC-backed queries (distinct from the Martin-tile cache-first service worker shipped 2026-08-21 in §0.21 -- no overlap). Phase 1 (allowlist + SWR engine) is shipped and live. Phase 2 (ETag/304, DW-revision headers on `environmental.ts`/`wildfire.ts`) is confirmed **not built**; needs rescoping to target whatever router S20's DuckDB/Polars serving path ships as a replacement, rather than the current one. |
| [upstream_dataset_expansion_20260806](tracks/upstream_dataset_expansion_20260806/) | blocked | feature | VPD, GloFAS, CAMS and Ensemble upstream lanes. Still valid, needs rescoping -- not superseded. Code for all four exists (shipped 2026-08-06, commit `c01ed48`) but the track's own 2026-08-06 review already recorded it as built-and-blocked: no `historical_writer.py` persist verbs, a still-unwritten CHECK-constraint migration, an unimplemented et0 structural guard, and no serving-side reads. No progress evidenced since. GloFAS and CAMS do not map cleanly onto any of §0.24.2's 11 named lanes -- an open scoping question for the owner. |

## Completed Tracks & Retrospectives

| Track | Status | Type | Summary |
|-------|--------|------|---------|
| [agri_sdk_layering_20260805](retros/agri_sdk_layering_20260805/) | historical | refactor | Phases 0-3 shipped and remain load-bearing. Phases 4-8 were explicitly superseded by the 2026-08-22/23 package-boundary decisions; the track metadata is archived. Current thin-CLI, import-lattice and canonical-core follow-up is owned by `repository_conformity_hardening_20260901`, not by reviving this retrospective. |
| [soil_survey_lane_shape_20260825](retros/soil_survey_lane_shape_20260825/) | complete | design-decision | Resolved by `68da7af`: soil survey is a version-stamped static release with bounded streaming parts and z0/z5/z9/z13, and the 200,000-key cap was deleted. Production route/client/browser cutover remains in the pivot track, not this design track. |
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
| [webworker_webgpu_acceleration_20260814](retros/webworker_webgpu_acceleration_20260814/) | completed (reverted) | feature | Dedicated Web Worker + WebGPU rendering pipeline. Built and integrated 2026-08-14/15, then reverted: every result it computed was discarded (`void`), so the "offload" ran synchronously on the main thread, and the worker module was imported directly rather than instantiated, installing its message listener on `window`. `LayerManager.tsx:718-725` now permanently forbids re-importing it. Archived 2026-08-22 during the pivot-reconciliation pass; the pivot independently removes any remaining motivation to retry (owner, §0.21.5: "we may not really want to invest much more in something we are migrating to parquet"). Orphaned files (`src/workers/layer-processor.worker.ts`, `src/lib/map/webgpu-accelerator.ts`) are dead code, flagged for deletion by whoever next owns those directories -- not deleted here (out of this pass's `conductor/`-only scope). |

## Pivot reconciliation notes — 2026-08-22

Written while auditing the seven tracks in `tracks/` against `conductor/RUNBOOK.md`
§0.23 (the Parquet/DuckDB/Polars pivot) and §0.24 (the 21-stream execution plan).
Full evidence for each is in that track's `metadata.json` under
`pivot_reconciliation_20260822`; this is the index.

- **`agri_sdk_layering_20260805` vs §0.24 — the load-bearing reconciliation.**
  This track is not hypothetical-overlap with the lane plan; it is
  **partially executed already**. `foundation/`, `method/ml/` and
  `method/monte_carlo/` exist in the tree with real code (landed in commits
  `7d917d0` and `4a685a1`, 2026-08-14/15), and `tests/test_layer_import_contract.py`
  enforces the layering. That is Phases 0-3 of the track's own 9. Phases 4-8
  — moving `db/`, `models/`, `ingest/`, the `historical_*` family and `cli.py`
  into `warehouse/`, `pipeline/`, `planes/`, `interface/` — did **not** ship;
  those four directories exist only as stub `AGENTS.md` + empty `__init__.py`
  pairs. §0.24.1 assigns those same source trees (`.../execution/**`,
  `.../ingest/**`) to S16 and reads them from S1-S15, using the pre-refactor
  path names — meaning §0.24 was written without accounting for what Phases
  0-3 already moved. Recommendation: **§0.24 governs going forward**; do not
  resume agri_sdk_layering Phases 4-8 as specified, since they would
  restructure the exact trees 16+ concurrent streams are about to write
  against. Phases 0-3's output should stay as-is — it doesn't collide with
  any declared S0-S21 boundary. One concrete, already-real conflict needs an
  owner call: §0.24.5 says ML "moves to `.../agri_data_service/ml/`", but ML
  code already lives at `method/ml/` (nested, from Phase 3) — the two plans
  disagree on ML's home path today, not hypothetically.

- **`swr_indexeddb_dw_reconciliation_20260814` and
  `webworker_webgpu_acceleration_20260814` vs the 2026-08-21 cache-first
  service worker (§0.21).** Checked for overlap; found none for the SWR
  track — the service worker caches Martin-served MVT tiles (cache-first,
  consumer-refreshed), the SWR track caches tRPC query results
  (background-revalidating IndexedDB persister) — different serving paths by
  design. The webworker/WebGPU track has no relationship to the service
  worker at all; its own history (built, integrated, reverted before the
  pivot even started) is unrelated to §0.21 and is documented in its own
  retro.

- **`community_engagement_completion_20260805`'s `interventions` (0
  published rows) and `sensors` (restored) layers** — checked directly.
  Sensors: fully restored, including the `geo.sensor_tiles()` SELECT-list fix
  (`drizzle/0010`) the track's own plan flagged as outstanding. Interventions:
  still 0 published, but the more precise finding is that 2 rows exist at
  `status='approved'` with no path to `'published'` — a moderation-workflow
  gap, not an empty layer with no producer.

- **Tracks with no direct pivot interaction, reconciled for completeness
  only:** `cds_only_products_20260808` and `upstream_dataset_expansion_20260806`
  (new upstream data sources, valid regardless of storage backend, but their
  eventual persistence target moves from Postgres to Parquet lanes) and
  `mycelium_cloud_seeding_spike_20260802` (unrelated domain; left `blocked`
  pending an owner decision the track's own plan already calls for, not
  archived on this pass's own authority).

No track in this batch was found fully complete against its own acceptance
criteria; `webworker_webgpu_acceleration_20260814` is the one archival this
pass made, and its "completed" outcome is a revert, not a shipped feature —
see its retro for the distinction.
