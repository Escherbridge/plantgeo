---
type: track-index
---

# PlantGeo current work registry

This is the only current Conductor track registry. Status uses the vocabulary in
[`README.md`](./README.md); implementation details and evidence stay in the
linked authority.

| Status | Track | Authoritative scope | Next gate |
| --- | --- | --- | --- |
| blocked | [Release certification](./release-governance.md) | Production release policy and exact gates | PostgreSQL 18 restore/extension proof, certified data release, reviewed exact SHA, separate authorization |
| blocked | [Forecast validation and Railway predeploy](./tracks/forecasting_predeploy_20260722/) | Local, governed metric-forecast evaluation and Railway rehearsal | Independent PostgreSQL 18 restore/parity proof; no Railway mutation |
| active | [Seasonal forecast and residual feedback](./tracks/seasonal_forecast_feedback_20260726/) | Evaluation-only seasonal candidates and time-honest residual-feedback design | Phase 0 read-only DSN or frozen checksummed export |
| planned | [North American intervention evidence](./tracks/north_america_intervention_data_20260723/) | Licence- and resolution-aware evidence expansion | Per-source adapter, coverage, and licence gate |
| blocked | [Strategy-selection governance](./tracks/strategy_selection_governance_20260726/) | Research-only strategy label, training, and selection lineage | Governed intervention/control outcome mapping and released labels |

## Historical backlog

The remaining numbered tracks and the older service/RAG plans are retained under
[`tracks/`](./tracks/) as product-history and discovery material. They are not
approved execution plans, release authorities, or evidence of an implemented
feature. In particular, legacy Track 18 does not authorize deploy-on-merge or
migration-on-deploy, and legacy strategy-card/RAG plans do not authorize ranked
strategy recommendations or efficacy claims.

Use [`tracks/README.md`](./tracks/README.md) only to navigate that retained
catalogue. Promote a historical item by creating a governed track with current
constraints and adding it to this registry; do not reactivate it by checking
boxes in the historical plan.
