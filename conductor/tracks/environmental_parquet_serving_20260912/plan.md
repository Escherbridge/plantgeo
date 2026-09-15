---
type: track-plan
slug: environmental_parquet_serving_20260912
status: active
updated_on: 2026-09-13
resource: ./spec.md
---

# Plan

## September 13 freshness audit and resolution

The [read-only audit](evidence/freshness-audit-20260913/audit.md) confirms missing static soil
publication, disabled perimeter refresh, a held sensor lane and cross-layer historical gaps.
The [resolution plan](freshness-resolution-plan-20260913.md) sequences runtime recovery,
publication reconciliation, source-direct historical repair, static soil restoration and acceptance.
Audit complete; implementation and production recovery remain open. Herbaria and intervention
work retain their existing owners and in-flight files.

## Active work

- Remove remaining environmental relational schemas, migration history, SQL readers and writers, compatibility commands, and PostgreSQL fallback options.
- Reconcile the layer catalogue against direct Parquet writers and readers. Stub an unavailable Parquet response where a reader is incomplete.
- Bind every historical and forward horizon to a direct source owner, then repair gaps with immutable source and publication receipts.
- Verify selected-day API, map, and agent behavior at each supported resolution.
- Complete weather forecast admission as a separate run-time and valid-time Parquet product, then validate its traditional forecast presentation.

## Sequencing

1. Finish repository and migration-state pruning.
2. Finish direct-writer and governed-reader coverage by layer.
3. Repair and publish missing history and forward windows.
4. Run multiscale, browser, agent, and production acceptance.

## Exit evidence

- Data-boundary, type, lint, and test checks from the final integrated tree.
- A relation census proving environmental payload relations and materialized views are absent.
- Per-layer publication and serving receipts for required days and resolutions.
- Production schedule and browser acceptance receipts owned by their active tracks.
