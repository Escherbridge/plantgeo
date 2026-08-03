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
