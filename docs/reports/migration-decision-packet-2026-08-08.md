# Migration decision packet — 2026-08-08

Five independent work lanes this session each hit a schema- or grant-level need they correctly
refused to freelance. This packet consolidates them into **one reviewed migration** (drizzle for
`geo`, alembic `0019` for `agri`) plus the one **owner decision** that gates it.

Nothing here is applied. Migrations auto-apply to production on push (`preDeployCommand`), so
this ships only after sign-off.

---

## The owner decision that gates everything: a training role

**Finding (ML lane, verified):** no existing role can complete the new
`forecast-train-wind --persist` chain, and **every covariate function is
`REVOKE EXECUTE ... FROM PUBLIC` with no grant to any named role** — nothing can read the
feature plane today.

- `plantgeo_forecast_writer` can INSERT the gated rows but cannot execute the validators.
- `plantgeo_forecast_publisher` can execute validators but cannot INSERT.
- Both validators are `SECURITY INVOKER`, so the caller also needs the UPDATE columns.

**Tension:** the 2026-08-03 architecture decisions recorded "no custom DB roles" as settled.
The forecast-role family (`writer`/`publisher`/`iteration`/`mv_refresh`/`reader`) predates that
decision and is load-bearing (the readiness probe asserts its privilege matrix), so the options
are:

| Option | Effect | Recommendation |
|---|---|---|
| **A. New `plantgeo_forecast_trainer` role** | Preserves writer/publisher separation of duties; the full GRANT script below is ready | **Recommended** — the "no custom roles" decision was about not inventing roles to make code work; this is a reviewed role for a genuinely new capability, mirroring the existing family |
| B. Widen `plantgeo_forecast_writer` | Fewer roles, but the writer gains validator execution — collapses the two-phase gate the schema was designed around | Not recommended |
| C. Defer — train only via the dev role locally | Zero migration, but `--persist` can never run under least privilege, and the readiness probe can never assert the training plane | Acceptable stopgap only |

**If A:** the ML lane's exact GRANT script (feature-plane function EXECUTEs, table
SELECT/INSERTs, the two validator EXECUTEs + their `SECURITY INVOKER` column UPDATEs, and the
deliberate non-grants on everything publication-facing) is in its report and reproduced in
`services/agri-data-service/src/agri_data_service/execution/AGENTS.md`. The same migration must
add matching entries to `FORECAST_ROLES` / `FORECAST_ROLE_*_PRIVILEGES` in
`routes/health/contracts.py` or the readiness probe reports the new role as unexpected.

---

## Uncontroversial items (no decision needed, just review)

1. **Local loader role grants** (readiness lane, critical): the documented local
   `local_source_loader` role cannot run any `ingest-*`/`jobs-*` verb — it lacks `geo` schema
   access and all `agri.job_*` grants. Until fixed, the operator guide's local smoke test
   demonstrates a permission error, and the only working backfill path is the production proxy.

2. **`receiver_writer` dashboard grants** (dashboard lane): `SELECT` on `agri.job_work_item`,
   `agri.job_attempt`, `agri.job_checkpoint` — without them the deployed `/ops/backfill` shows
   its permission banner instead of data. Also add the rows to `PUBLICATION_TABLE_PRIVILEGES`
   in `routes/health/contracts.py`.

3. **Claim-path index** (performance lane, exact DDL filed):
   ```sql
   CREATE INDEX ix_job_work_item_claimable
     ON agri.job_work_item (job_run_id, priority DESC, available_at, id)
     WHERE status IN ('queued','retry_wait','deferred','leased','running');
   ```
   The existing `ix_job_work_item_claim` matches neither the claim's `job_run_id` predicate nor
   its sort. Low urgency at one shard/tick; matters when a fast-handler lane (e.g. training)
   raises claim frequency.

4. **`consecutive_parks` column** (hardening lane): the park bound currently derives its count
   from `job_attempt` × `job_checkpoint` per park; a column makes it one increment and lets
   `jobs-status` show it without a join. Optional companion: a `job_incident` writer with
   fingerprint/cooldown for the alerting path (audit finding: no failure signal ever reaches a
   human), and triggers to make `job_run.updated_at` honest.

## Apply path (when approved)

`db/agri/**` edits → alembic `0019` loading them via `load_object_sql` → `db/tools/regenerate.py`
against a disposable DB → parity test green → drizzle migration for anything `geo`-side →
`routes/health/contracts.py` matrices updated in the same commit → push (Railway migrates prod).

## Explicitly out of scope until decided separately

- Per-issue-date as-of gating for the covariate plane (`agri_covariates_v2`) — the recorded
  revision-leakage fix; a schema-version bump, not a patch.
- `forecast_quality_policy` seeding — `--persist` requires a policy key and there is
  deliberately no seeder; inventing pass thresholds is fabricating governance.
