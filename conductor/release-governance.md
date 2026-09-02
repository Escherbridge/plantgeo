---
type: governance-policy
---

# Release governance

This is the sole Conductor authority for production certification. It supersedes
the legacy auto-deploy and migration-on-deploy statements in
`tracks/18-railway-deployment/`; that material is historical platform backlog.

## Current state — reviewed 2026-07-26

Production data is **not certified**. Code deployment is no longer gated by a
Conductor switch: the operator moved `plantgeo-main` to a single Railway path
where a push to `main` builds, runs the in-build quality gates, applies pending
Drizzle migrations through `deploy.preDeployCommand`, and must pass
`/api/ready`. Data certification is unaffected — a green deploy does not certify
sources, forecasts, or labels. Local schema governance is implemented through
`20260725_0013`; that does not prove production database parity.

| Gate | Current state | Authority / evidence |
| --- | --- | --- |
| Build gates, pre-deploy migration, and readiness gate | implemented, always on | `Dockerfile`, `railway.json`, `docs/deployment.md` |
| Production PostgreSQL 18 backup/restore and extension parity | blocked | `tracks/forecasting_predeploy_20260722/plan.md` |
| Certified source/release lineage | blocked pending separately reviewed production handoff | `docs/data-ingestion-and-serving-contract.md` |
| Forecast validation and publication | blocked; candidates remain evaluation-only | `tracks/forecasting_predeploy_20260722/` |
| Intervention-effect labels and strategy efficacy | blocked; no governed outcome labels located | `tracks/strategy_selection_governance_20260726/` |

## Release rule

A successful build, deploy, local migration, evaluation artifact, or historical
track does not certify data. Data certification requires all applicable gates
above, the exact release revision, a reviewed rollback/observability plan, and
separate operator authorization. The Drizzle `preDeployCommand` is the one
automatic migration the operator has authorized; do not enable a forecast
publication, scheduler, or `effect_candidate` finalization as part of Conductor
maintenance.

## Scheduler-owner directive — 2026-09-02

The owner has explicitly authorized the repository release that makes
`plantgeo-job-executor` the sole scheduler and durable invocation owner for PlantGeo data work.
That authorization rejects Railway cron scheduling and authorizes controlled retirement of the
six inventoried legacy scheduled/one-shot writer service objects after, and only after, all of the
following gates are satisfied:

1. the reviewed scheduler release is merged to `main`;
2. the exact `plantgeo-job-executor` deployment for that `main` commit reaches `SUCCESS`;
3. the executor registry, schedules, source ceilings, leases, checkpoints, retries/dead letters,
   idempotent publication, restart/catch-up behavior and no-overlap tests are green;
4. a fresh production read proves no legacy writer invocation or executor lease is in flight;
5. the orchestration task gives the explicit post-deployment follow-up to perform the handoff; and
6. every replacement lane is observed running successfully before its former service object is
   removed.

The authorization is directional, not an immediate production mutation. A feature-branch build,
review, or green deployment of another commit does not satisfy it. Rollback disables the affected
executor lane and preserves its PostgreSQL/R2 data, manifests and checkpoints. Rollback must never
restore a `cronSchedule`, reconnect an old writer, or recreate a Railway cron/one-shot writer
service. The scheduler handoff evidence and exact production order live in
`tracks/gapless_parquet_publication_20260901/evidence/scheduler-handoff-20260902.md`.
