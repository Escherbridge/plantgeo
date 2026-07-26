---
type: governance-policy
---

# Release governance

This is the sole Conductor authority for production certification. It supersedes
the legacy auto-deploy and migration-on-deploy statements in
`tracks/18-railway-deployment/`; that material is historical platform backlog.

## Current state — reviewed 2026-07-26

Production data is **not certified**. The production deploy workflow remains
deliberately disabled unless both its explicit production switch and the exact
reviewed certification SHA are set. Local schema governance is implemented
through `20260725_0013`; that does not prove production database parity.

| Gate | Current state | Authority / evidence |
| --- | --- | --- |
| Exact revision checks and deployment switch | implemented, disabled | `.github/workflows/deploy.yml` |
| Production PostgreSQL 18 backup/restore and extension parity | blocked | `tracks/forecasting_predeploy_20260722/plan.md` |
| Certified source/release lineage | blocked pending separately reviewed production handoff | `docs/data-ingestion-and-serving-contract.md` |
| Forecast validation and publication | blocked; candidates remain evaluation-only | `tracks/forecasting_predeploy_20260722/` |
| Intervention-effect labels and strategy efficacy | blocked; no governed outcome labels located | `tracks/strategy_selection_governance_20260726/` |

## Release rule

A successful build, local migration, evaluation artifact, or historical track
does not authorize a production release. Certification requires all applicable
gates above, the exact release revision, a reviewed rollback/observability plan,
and separate operator authorization. Do not enable an automatic migration,
forecast publication, scheduler, or `effect_candidate` finalization as part of
Conductor maintenance.
