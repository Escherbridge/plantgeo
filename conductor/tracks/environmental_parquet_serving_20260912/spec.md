---
type: track-spec
slug: environmental_parquet_serving_20260912
status: active
---

# Environmental Parquet serving

## Purpose

Make governed Parquet the complete storage and serving boundary for environmental data. Source acquisition, validation, backfill, forward refresh, gap repair, availability publication, API reads, map layers, and agent tools must operate without PostgreSQL observation tables, archive readers, writer fallbacks, or compatibility paths.

PostgreSQL remains available for transactional application data, community interventions, job control, and small reference lookups that are explicitly outside the environmental payload plane.

## Required behavior

- Every environmental writer publishes source-derived Parquet directly with immutable receipts and required resolution rungs.
- Every environmental reader uses the governed selected-day capability and returns explicit missing, unavailable, source-ceiling, or unsupported states.
- No request or job may fall back to PostgreSQL when a Parquet product is absent or incomplete.
- Gap detection creates bounded repair work from the original source. It does not promote a relational archive.
- Availability advances only after physical objects and completion markers validate.
- Agent tools use the same selected day, spatial bounds, provenance, and missingness semantics as the product UI.

## Completion gates

1. Repository searches and boundary checks find no environmental PostgreSQL readers, writers, schemas, migrations, commands, or compatibility flags.
2. Each declared layer has direct source ingestion, forward refresh, gap repair, coverage publication, serving registration, and agent-tool registration.
3. Cold and warm API traces prove selected-day and supported-zoom behavior for populated, governed-absence, unavailable, and source-ceiling cases.
4. Production schedules show three consecutive successful advances for active products, with no retired writer owner or in-flight legacy work.
5. The production acceptance track records browser, conservation, freshness, and release evidence.

## Non-goals

This track does not move authentication, teams, community workflows, interventions, operational job state, or approved species reference profiles into Parquet.
