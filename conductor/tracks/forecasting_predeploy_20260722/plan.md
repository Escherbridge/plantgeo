---
type: plan
---

# Forecast validation and Railway predeploy plan

## Current state — reviewed 2026-07-26

This plan records historical implementation evidence through the original
forecasting revisions. The current local governance head is `20260725_0013`.
Forecast candidates remain evaluation-only and unpublished; production remains
blocked on a verified PostgreSQL 18 backup/restore and extension-parity drill.
Strategy-selection governance and the label-source abstention are recorded in
[`strategy_selection_governance_20260726`](../strategy_selection_governance_20260726/).
Seasonal candidate work continues only through the evaluation-only
[`seasonal_forecast_feedback_20260726`](../seasonal_forecast_feedback_20260726/)
track.

## Completed local evidence

- [x] Review migration `20260722_0005`, ORM parity, tests, and SQL framework docs.
- [x] Create and verify a custom-format pre-migration backup before applying forecasting changes.
- [x] Add and apply `20260722_0006` for immutable historical hindcast outcomes and typed signals.
- [x] Add and apply `20260722_0007` to enforce forward-only calibration availability.
- [x] Register a pinned NASA POWER WS2M point series with exact release and spatial-support lineage.
- [x] Execute 14 deterministic seven-day rolling-origin hindcasts over real pinned history.
- [x] Finalize 14 immutable receipts and 98 forecast-versus-actual signal values.
- [x] Reject the first candidate after the broader historical gate; publish zero forecast rows.
- [x] Add reviewed writer, publisher, reader, and materialized-view refresh capability roles.
- [x] Add a dedicated local refresh operator and verify explicit unscheduled refresh.
- [x] Add broad local developer and read-only PgAdmin viewer roles without granting superuser.
- [x] Add a typed, bounded published-only forecast serving route for agent, chart, and map consumers.
- [x] Advance readiness and CI migration expectations through revision `20260722_0007`.
- [x] Document the Railway PostgreSQL 18 predeploy, backup, role, receipt, observability, and rollback gates.
- [x] Complete independent code/readiness review and record unresolved findings below.
- [x] Pass Python format/lint/type checking, focused repaired tests, web type checking, data-boundary checks, 140 web tests, and the Next.js production build.
- [x] Add `20260722_0008` for active-policy/calibration enforcement, versioned receipt digests, and production function privilege hardening.
- [x] Add separate receiver/writer and published-reader DSN/readiness profiles with profile-specific route mounting.
- [x] Prove clean `0008` migration/function behavior and structural v1 checksum preservation in guarded disposable PostgreSQL 16 databases, then drop them by exact name.
- [x] Complete an independent review of the continuation changes and preserve the review findings.

## Continuation status and remaining blockers

- [x] Run migrations through `20260722_0007` and representative forecast function tests in a disposable PostgreSQL database, then drop only that verified disposable database.
- [x] Re-execute the corrected evaluation-only fixture against the existing exact-source series under new governed candidate/model/policy/run identities in the guarded disposable database; it created rejected v2 run `3a3723bc-a039-4b22-b564-25ebd4edbf57` without duplicating the source identity or touching live rejected v1 run `598466ea-1181-4772-8f30-f46574dce1e9`.
- [x] Require an active quality policy and at least its configured calibration sample count in the hindcast finalizer.
- [x] Bind declared series/model/policy identifiers and definition, horizon, training cutoff, and actual availability fields into the externally verifiable receipt digest; reject new v1/direct-finalized inserts and freeze policies referenced by finalized receipts.
- [x] Separate production receiver/writer and published-reader DSNs/readiness profiles; production profiles reject the legacy combined DSN.
- [ ] Rehearse the verified custom-format restore and PostgreSQL 18 extension parity before any Railway migration.
- [x] Run the stricter corrected gate: 3/14 origins passed and interval coverage was `0.6326530612`, so v2 was rejected and created zero forecast receipts, values, publications, or pointers.
- [x] Add a metadata-enriched canonical time-series view and release/as-of contract.
- [x] Add a daily date spine with explicit gap policy, coverage, and row-level lineage.
- [x] Add deterministic 30-day empirical-bootstrap low/median/high transformations.
- [x] Persist evaluation-only iterations and later actual/residual signals with
  idempotent stored procedures.
- [x] Prove the 30-day loop in the disposable warehouse database without creating any
  operational forecast receipt, publication, or pointer.
- [x] Complete independent review and one integrated validation sweep.

## Explicitly deferred

- [ ] Do not deploy or mutate Railway from this track.
- [ ] Do not schedule materialized-view refresh or forecast execution yet.
- [ ] Do not promote strategy recommendations or populate `strategy_selection`.
- [ ] Do not delete the named warehouse volume, raw receipt cache, historical Parquet lake, or older backups while pruning task context.

## Follow-on track

Seasonal candidates and time-honest residual-feedback signals are planned in
[`seasonal_forecast_feedback_20260726`](../seasonal_forecast_feedback_20260726/).
They remain evaluation-only and start only after a read-only data-quality audit
establishes enough independent scored origins for a frozen final holdout.
