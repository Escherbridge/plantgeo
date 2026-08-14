---
type: specification
---

# Forecast validation and Railway predeploy

## Goal

Produce a forecast only when pinned historical evidence beats the governed baseline,
while retaining every finalized historical forecast-versus-actual outcome as an
immutable, time-honest signal for later ML training. Rehearse the database and
serving path locally without deploying or changing Railway.

## Invariants

- Historical hindcasts are separate from operational forecast runs; simulated issue
  times must never be inserted as operational `forecast_run.issue_time` values.
- A hindcast may use only training and calibration observations available at its
  simulated cutoff. The actual may become a signal only at its real availability time.
- Publications are immutable, receipt-bound, and derived only from a run that passes
  every configured quality gate. A failed run remains queryable but unpublished.
- The first deliverable is a metric forecast. `strategy_selection` and every strategy
  recommendation remain gated and out of scope.
- The local warehouse may have convenient non-superuser developer and read-only viewer
  roles. Production capability roles remain least-privilege and `NOINHERIT`.
- Railway work in this track is documentation and local rehearsal only. No service,
  variable, database, deployment, or schedule may be mutated.

## Acceptance criteria

- Exact source variant, support, release set, source release, source data, cell, and
  temporal coverage are recorded for each candidate.
- Deterministic rolling-origin hindcasts compare the SQL linear baseline with naive
  last-value forecasts and persist empirical uncertainty/outcome signals.
- Immutable receipt checksums can be recomputed independently, and signal queries are
  typed and availability-aware.
- Forecast writer, publisher, reader, and materialized-view refresh capabilities are
  independently reviewable; PUBLIC has no forecast mutation privilege.
- Published forecast serving is typed, bounded, and reads only the governed published
  view. Materialized-view refresh is explicit and unscheduled.
- The Railway checklist covers PostgreSQL 18 extension parity, backup/restore,
  migration guards, roles, promotion receipts, observability, and rollback.

## Generic 30-day iteration contract

The runnable evaluation path begins with a canonical SQL view that enriches every
registered forecast series with provider, licence, entity, metric, unit, native
support, requested grain, and release metadata. A release-set/as-of function
applies the governed visibility boundary. A daily date spine then aligns
observations without collapsing missing calendar days and binds its gap policy and
source-row lineage into the derived-row checksum.

Forecast algorithms consume that generic aligned contract. The first implementation
is a deterministic empirical bootstrap over historical daily increments. It emits
30 daily low/p10, median/p50, and high/p90 values from checksum-derived draws; it
does not use session-global randomness or claim a validated probabilistic model.
The method, seed, simulation count, horizon, bounds, gap policy, cutoff, release
set, and input lineage are checksum-bound.

A stored procedure persists each calculation into an append-only, evaluation-only
iteration and value plane. It has no foreign-key or function path into
`forecast_publication_item`. A separate reconciliation procedure appends actual
observations when they are available, while a typed signal contract exposes
forecast, actual, residual, error, and interval-coverage series using honest
availability times. New iterations may use accumulated history and outcome signals;
past predictions are never rewritten to appear more accurate.
