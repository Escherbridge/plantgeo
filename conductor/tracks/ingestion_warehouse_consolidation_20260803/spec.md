---
type: track-spec
track: ingestion_warehouse_consolidation_20260803
status: active
---

# Ingestion & warehouse consolidation — specification

The governing detail lives in [`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../plans/ingestion-warehouse-consolidation-2026-08-03.md)
(7 phases, DDL sketches, risk register, open questions) and in
[`services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md`](../../../services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md)
(the measured cut list). This file records only what the registry needs: the
settled decisions and the gates.

## Goal

One database. Everything presented is persisted and served by us. The warehouse
is right-sized for a solo-dev research tool. The map gains a day-granular slider
running from the start of ingested history to +30 days.

## Settled decisions

These are closed. Implement them; do not re-open them.

| # | Decision |
|---|---|
| D1 | Geometry is a conformed dimension. One `geo.geometry` table; everything else is a fact keyed to `geometry_id`. A grid cell is an ordinary geometry row. |
| D2 | The map forecast is a time slider, not a metric. One day-granular slider, past → today → +30 d. A toggle picks statistical vs ML for future days only. |
| D3 | MTBS burn severity is in scope. Persist it; native key `Fire_ID`; `data_available_at` comes from the annual release date, never `Ig_Date` (which leaks ~18 months). |
| D4 | The evaluation-only lock is removed. `purpose` stays a plain column, filtered in serving so a backtest cannot surface as a live forecast. |
| D5 | The ML serving lane survives. All nine tables `v_forecast_series_serving` joins are kept as plain storage; only the trigger/guard/finalize machinery on them is removed. No intermediate state in which the ML view is dead. |
| D6 | The geometry dimension is Type-2. `natural_key` identifies the place across time; `geometry_id` identifies one version. |

Also settled: no custom database roles; `plantgeo-dataservice` is deleted and
Python is a local/batch CLI, never an HTTP service; rendered cartographic
basemaps (ArcGIS World Imagery) are the one exception to "we persist what we
serve"; slider depth is `min(version_valid_from)` shown dynamically and "today"
is server UTC; clean up dead code as you touch it.

### Deviation recorded 2026-08-03

The plan file's §2 A2 "Still cut" list includes the strategy/intervention planes
(Option 3 scope from the checksum audit). The session handoff instead accepted
**Option 4** — same cut minus the strategy/intervention teardown. Option 4 is
what phase 2 implements: those zero-row planes and their checksum functions are
retained, because the strategy-selection plane is label-blocked rather than
dead and may be revived against a different target. Everything else in the plan
file's cut list stands, including the hindcast plane, the four owner roles, the
two `GENERATED … STORED` columns and the eval-only CHECKs.

## Non-negotiable gates

- **The ML view is never dead.** `REFRESH MATERIALIZED VIEW agri.mv_forecast_ml_daily_serving`
  must succeed after every Alembic revision in phase 2, including intermediate ones.
- **`DROP FUNCTION` without `CASCADE`**, so a missed dependency surfaces immediately.
- **Retain `ck_forecast_receipt_finalized_evidence` — and note that it had to be repaired
  before it could carry that weight.** `forecast_receipt.receipt_checksum` has no SQL
  function behind it, so with the guards gone this CHECK is the only thing preventing
  `status='finalized'` on an empty receipt, and the serving view trusts that status.
  As originally written it did **not** prevent it: a NULL `receipt_checksum` made the
  predicate evaluate to NULL rather than FALSE, and a CHECK rejects only FALSE. `0018`
  rebuilds it with an explicit `receipt_checksum IS NOT NULL` conjunct. Treat any
  "this constraint protects us" claim as unverified until someone has watched it reject
  the NULL case.
- **Every Drizzle migration updates `src/lib/server/db/migration-contract.ts` in
  the same commit**, or `/api/ready` fails its hash check and Railway kills the release.
- **No literal row counts in assertions.** Ingestion runs on a cron, so any count
  written down is stale within hours. Capture the baseline inside the same transaction.
- **Production migrations are applied by the owner.** Agents verify against the
  pg18 rehearsal container at `127.0.0.1:5445`.

## Known blockers carried forward

- The ML lane has never run: nothing in `src/` writes `forecast_receipt` or
  `forecast_publication`, so preserving the nine tables is necessary but not
  sufficient. Phase 7 must ship a CLI publisher, not just a model.
- The `0013` strategy-selection plane has zero labelled rows, so the existing ML
  target is not trainable. A different target may need defining; that is not
  scoped here.
