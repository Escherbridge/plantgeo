---
type: work-registry
---

# Current work registry

Per [`README.md`](./README.md), this file is the sole current work registry.
Material under `tracks/` predating this registry remains historical backlog
unless promoted here. Status vocabulary: active, planned, blocked, complete,
historical. A status is updated here and in the track's `metadata.json`
together.

Registry created 2026-08-02.

| Track | Status | Type | Summary |
|-------|--------|------|---------|
| [mycelium_cloud_seeding_spike_20260802](tracks/mycelium_cloud_seeding_spike_20260802/) | active | research-spike | Feasibility spikes for INA-fungal products relying on natural spore transport. Spikes 001-005 executed 2026-08-02 (verdicts in `spikes/README.md`): concept survives only in reframed form — agronomic product with measurable regional INP enrichment, not event-scale cloud seeding. 006 deferred pending user decision. |
| [ingestion_warehouse_consolidation_20260803](tracks/ingestion_warehouse_consolidation_20260803/) | active | feature | Cut the enforcement layer while preserving the ML serving lane, key everything to one Type-2 conformed geometry dimension, port ingestion to the Python CLI, and serve past→future through one day-granular slider. Governing detail in `plans/ingestion-warehouse-consolidation-2026-08-03.md`. Phase 2 (Alembic `0018`) in progress; it is destructive to `agri` and free only while `agri` holds 0 rows. |
