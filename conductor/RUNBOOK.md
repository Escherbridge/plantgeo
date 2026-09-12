---
type: runbook
status: active
updated_on: 2026-09-12
---

# Current operating runbook

## Directive

Environmental payloads write directly to governed Parquet and read only from the Parquet serving plane. PostgreSQL environmental observation tables, materialized views, archive readers, writers, migrations, and fallback options are retired. Missing or incomplete Parquet coverage must produce an explicit unavailable or governed-absence response and a bounded source-direct repair task.

PostgreSQL remains for transactional application data, community interventions, operational job state, and approved small reference lookups such as species profiles.

## Outstanding work

| Area | Owning track | Next proof |
| --- | --- | --- |
| Repository and schema boundary | [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/plan.md) | Zero environmental PostgreSQL readers, writers, schema objects, migrations, compatibility commands, or fallback flags; clean bootstrap and relation census. |
| Historical and forward coverage | [Gapless publication](tracks/gapless_parquet_publication_20260901/plan.md) | Every owed day is immutable data or a governed absence; repair owners and three consecutive scheduled advances are recorded. |
| Slider, API, and agent reads | [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/spec.md) | Cold and warm traces for selected day, viewport, supported zoom, spatial neighbours, temporal neighbours, missingness, and source ceilings. |
| Multiscale rendering | [Multiscale surfaces](tracks/multiscale_polygon_surface_20260901/plan.md) | Live conservation, continuity, readability, hover, performance, and mobile evidence at every required rung. |
| Weather observations | [Platform QA](tracks/platform_experience_qa_20260911/plan.md) | Reconcile the incomplete selected day against availability, then capture populated desktop and mobile behavior. |
| Weather forecast | [Forecast lane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) | Admit Open-Meteo run-time/valid-time data to Parquet, serve fields and location forecasts, and render continuous weather, wind, hourly, and daily information beyond the observation horizon. |
| Botanical profiles | [Species profile lookup](tracks/botanical_species_profile_lookup_20260911/plan.md) | Inspect the authorized Railway lookup, admit provenance-bound growth and plant-composition sources, publish immutable profiles, and validate agent/API/MCP use. |
| Herbaria specimens | [PNW Herbaria admission](tracks/pnw_herbaria_source_admission_20260911/plan.md) | Resolve immutable releases, rights, coordinate policy, field maps, and quarantine controls before any specimen archive pilot. |
| Production release | [Production acceptance](tracks/parquet_production_acceptance_20260901/plan.md) | Cross-layer browser, freshness, schedule burn-in, conservation, rollback, and release verdict after upstream gates pass. |

## Operating sequence

1. Choose one layer and freeze its source, day horizon, resolutions, current publication generation, and owning schedule.
2. Read the physical Parquet objects, completion markers, and availability entry independently. Do not infer one from another.
3. If coverage is missing, create bounded repair work against the original source. Preserve source identity, request bounds, and checksums.
4. Publish all required rungs and completion receipts before advancing availability.
5. Verify the public selected-day reader, map rendering, and agent tool against the same generation. Exercise populated, absent, unavailable, and source-ceiling responses.
6. Record the evidence in the owning active track and update its metadata and this runbook only when the outstanding state changes.

## Recovery

- Disable the affected current schedule and preserve the last valid immutable generation and pointer.
- Re-run a bounded, idempotent source-direct request. Never restore a PostgreSQL environmental reader or writer.
- Do not advance availability until every required object and marker verifies.
- If a reader is incomplete, return an explicit Parquet unavailable response while the owning track repairs it.
- For release failures, keep the previous verified generation active and record the exact revision, request, source identity, and failed gate.

## Validation

Apply the complete change batch before the final integrated check. Run the data-boundary check, type check, lint, affected frontend and Python tests, migration/bootstrap verification, and relation census appropriate to the change. Production acceptance additionally requires cold and warm request traces, browser evidence, schedule burn-in, and an independent release verdict.
