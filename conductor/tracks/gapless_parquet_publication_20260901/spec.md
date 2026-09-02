---
type: track-spec
slug: gapless_parquet_publication_20260901
status: active
---

# Gapless Parquet forward publication and scheduler ownership

## Purpose

Turn the independently verified historical archives into continuously maintained products. One
stateful job-executor service owns durable schedules; each product still has three logical duties:
forward refresh, gap repair that authors work, and coverage status.

The 2026-09-01 production browser assessment found 27-day climate/NASA tails, 31-day ERA5 tails and
a 94-day shortwave-radiation tail. At that preflight Railway showed a healthy executor leader with
38 configured responsibilities but `active_lane_count=0`; historical manifests therefore could not
be treated as forward ownership. The 2026-09-02 handoff activated 37 executable lanes at exact
`main` release `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1`, with the snapshot-only soil-moisture
responsibility intentionally terminal. This establishes invocation ownership, not completion of the
observed tails or repair of the current matview/fire-perimeter runtime blockers.

## Terminal-day contract

For every owed day from the product floor through its source-specific ceiling, every required rung
must close as exactly one of:

1. immutable published data, receipts and completion markers; or
2. an immutable governed absence with source receipt and reason.

Missing, failed, unsettled, truncated and exhausted-source states remain explicit. A closed window
returns one ordered envelope per requested calendar day and never carries data from another day.

## Availability publication contract

Each time-bearing physical lane publishes an immutable generational `availability.parquet` with one
row per `(day, rung)` and a checksum-bound `_LATEST.json` pointer. Rows bind source and terminal
receipts, data/completion receipts and a nullable governed-absence reason. File metadata and pointer
bind the authoritative ordered required-rung set. Historical bootstrap is performed once from
verified manifests/checkpoints and writes an immutable receipt containing their keys/SHAs, source
inventory root and required rungs; generation zero and all successors bind that receipt's key/SHA.
Thereafter every forward, repair, backfill or governed-absence outcome extends the prior generation
only after all required data parts and completion markers are durable. The new index is re-read and
verified before the pointer advances last with a conditional write. Corrections create a new
generation; prior generations remain rollback evidence.

The request path may read the pointer and its one Parquet object. It may not list historical lane
objects, open historical data parts, query PostgreSQL or silently invoke the bootstrap census.
Invalid or absent availability fails closed. A separate scheduled audit may re-list history and
author repair work, but it is never a slider dependency.

## Scope

- freeze provider ceilings, lags and owner handoffs for every time-bearing product;
- build source-family direct writers and all-rung publication;
- author missing-day work and bounded tail repairs;
- register schedules, leases, checkpoints, retries and dead-letter visibility in one executor;
- remove every tracked Railway `cronSchedule` and cron-only image/config after registry parity tests;
- after the explicit post-deployment follow-up required by `conductor/release-governance.md`, activate
  the complete atomic responsibility set, observe every source/product owner, and remove the legacy
  service objects without overlap.

The owner directive dated 2026-09-02 authorizes the executor-only repository release and the
eventual controlled retirement of legacy Railway writer objects. It does not activate a feature
branch. `p5` remains operationally gated until its reviewed commit is merged to `main`, the exact
executor deployment reaches `SUCCESS`, the orchestration task supplies an explicit follow-up, and
the no-in-flight/no-overlap check passes. `p6` remains the production burn-in gate.

## Out of scope

- reader or renderer cutover;
- PostgreSQL data deletion, writer retirement or storage shrink;
- unbounded historical rewrites or overlapping replacement writers.

## Completion gates

- Every product has an explicit provider, floor, ceiling, lag, cadence and single active owner.
- Every owed day/rung is published or governed absent; no unexplained tail exceeds declared lag.
- Coverage status creates idempotent repair work rather than only reporting.
- Availability is bootstrapped once, extended transactionally by ingestion and readable without a
  historical LIST/data scan.
- Each activated lane advances over at least three schedules and survives retry/restart/lease expiry.
- Capability ceilings never extend beyond the provider ceiling.
- Exact inventory and aggregation conservation remain clean after advancement.
- Rollback preserves all immutable output and never overlaps old and new owners.
