---
type: task-custody
date: 2026-09-11
status: pending-reconciliation-review
---

# Legacy QA and repair task custody

The parent retained these two tasks in `PlantGeo Pending Reconcile` after the
host restart. Their source work belongs to legacy candidate
`3a5f3902e6f56b0878eab1d72b34789f6653058f`, whose complete preservation and
supersession must be reviewed before either task is safe to archive.

| Task title from the app | Stable task ID | Archive gate |
| --- | --- | --- |
| PlantGeo QA and orchestration | `01a08af2-569e-7532-a16c-79823255e487` | Reviewed legacy reconciliation, accepted selective repair, final combined verification and exact committed handoff |
| Repair PlantGeo Parquet ingestion and… | `01a08b00-2a50-73c2-b39b-39523c74ceb2` | The same code/evidence gates, with unfinished production and data operations explicitly retained by current owners |

Their completed local verification claims describe the older candidate only.
The [legacy reconciliation](legacy-candidate-reconciliation-20260911.md) binds
its file and behavior changes; the [integration ledger](integration-ledger-20260911.md)
records accepted replacements and the final candidate. Neither older task is
the implementation owner for another concurrent copy of these repairs.

The older release request is superseded by the current
[release packet](release-packet-20260911.md). It remains HOLD until the exact
candidate and production evidence satisfy its stated gates. Product publication,
signal/sensor corrections, older MTBS admission, static-soil restoration and
relation/service removal remain with
[environmental retirement](../../environmental_postgres_retirement_20260904/plan.md);
scheduler activation and recovery remain with
[gapless](../../gapless_parquet_publication_20260901/plan.md); the deployed
product/browser matrix remains with
[production acceptance](../../parquet_production_acceptance_20260901/plan.md).
Botanical source admission and runtime work retain their separate tracks and
are outside this environmental integration candidate.

**Current disposition: keep both tasks unarchived.** Replace this gate with the
exact reviewed integration revision before reporting them safe to archive.
Archiving a superseded task preserves its history and does not close the active
successor tracks or authorize a production action.
