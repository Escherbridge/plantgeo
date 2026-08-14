---
type: plan
---

# Conductor Track Execution Plan: In-App Postgres Job Runner & Platform Admin Control Panel

> **2026-08-14 — this plan was re-ticked after a fabrication audit.** Every box below had been
> checked while the work described was largely not real: the schema lived only in an unapplied
> Drizzle migration against a schema Drizzle may not touch, the scheduler polled a table nothing
> creates and invented its own `records_processed`, the tRPC router queried that same nonexistent
> table and swallowed the Python service's answer, `/admin` had no route gate at all, and the
> "Logs Inspector" showed schedule rows rather than any execution history. The rebuild below runs
> on the real substrate — the Alembic-owned `agri.job_definition` / `agri.job_run` ledger. Each
> item now records what was found and what was actually built.

## Phase 1: Schedule state (rebuilt: no new table, no new migration)
- [x] **REVERSED 2026-08-14.** `drizzle/0026_agri_job_schedules.sql` (creating `agri.job_schedules`
  plus five seeded lane rows) and the matching `jobSchedules` model in
  `src/lib/server/db/schema.ts` are **deleted**, along with the migration's journal entry. Alembic
  is the only component permitted to touch the `agri` schema (`jobs/AGENTS.md`), and the migration
  had never been applied to production (`to_regclass('agri.job_schedules')` → null there), so every
  procedure built on it would have thrown `relation does not exist` against a real database. The
  `agri` `pgSchema` declaration is gone from `schema.ts` too, so no Drizzle DDL can be emitted
  against that schema by accident.
- [x] **No migration authored, deliberately.** The pause/enable toggle the track needs is
  `agri.job_definition.enabled`, a column the ledger has carried since Alembic `20260719_0001`.
  The Alembic head is unchanged at `20260814_0023`, and `tests/conftest.py`'s
  `EXPECTED_ALEMBIC_HEAD` is therefore untouched.
- [x] **No seeded lane list.** `firms-fire`, `usgs-streamflow`, `era5-weather`, `usda-soil` and
  `usdm-drought` were rows in the deleted seed with no handler behind any of them. The admin panel
  now lists exactly what `agri.job_definition` holds, so a lane appears once its code registers it
  and never before.

## Phase 2: Python dispatch over the real ledger
- [x] `jobs/scheduler.py`: the `InAppScheduler` polling loop, `execute_lane_job` (which fabricated
  `records_processed = min(100, max_records)` and wrote only to the nonexistent table) and
  `hash_lane_id` are **deleted**. Nothing in `src/` names `agri.job_schedules` any more; the
  advisory-lock fencing they described is unnecessary because the real ledger claims work with
  `FOR UPDATE SKIP LOCKED` plus a lease fence (`jobs/lease.py`).
- [x] New `jobs/dispatch.py`: a lane-dispatch registry (`register_dispatchable_lane`,
  `LANE_DISPATCH`, `dispatch_lane`). Any lane joins with one call beside its `@job_handler`, and
  the trigger route holds no per-lane branch. A lane publishes a *trigger* rather than being
  auto-discovered from `JOB_HANDLERS` because every handler in this service resolves its side
  effects from a lane-bound `ContextVar` the ledger cannot supply — the module docstring argues
  this in full.
- [x] `strategy-mv-refresh` registers itself through that seam; its behaviour through
  `POST /api/v1/jobs/trigger` is byte-for-byte what it was (upsert definition → open/rejoin run →
  `run_job_slice`), now reached generically.
- [x] Pause is honoured in exactly one place: `dispatch_lane` reads
  `sql/jobs/select_job_definition_pause_state.sql` **before** calling a lane's trigger, because
  `ensure_job_definition` writes `enabled = EXCLUDED.enabled` and a trigger allowed to start would
  un-pause the lane as a side effect of its own upsert. `StrategyMvRefreshScheduler.run_once` goes
  through the same call, so pausing stops the timer as well as the button.
- [x] **Route path bug fixed.** `jobs_bp` declared `url_prefix="/api/v1/jobs"` while `app.py` mounts
  it inside a group already prefixed `/api/v1`, so the real path was
  `/api/v1/api/v1/jobs/trigger` and the documented endpoint 404'd. The prefix is now `/jobs`, like
  every other blueprint, and a test pins the mounted path.
- [x] `GET /api/v1/jobs/lanes` added: what the service can actually run, by name.

## Phase 3: tRPC router & Admin UI on real data
- [x] `src/lib/server/trpc/routers/jobs.ts` rebuilt against `agri.job_definition` / `agri.job_run`
  via raw SQL on the Next app's pool: `getLanes` (one row per definition name, its newest version's
  configuration, its aggregated enable state and its latest run), `toggleLane` (writes **every**
  version row of the name — pausing only the newest would fall through to an older enabled one),
  `triggerLane` and `getRunHistory`.
- [x] **Every procedure is `adminProcedure`.** `getJobSchedules` had been `protectedProcedure`, so
  any signed-in user could read the operations surface.
- [x] `triggerLane` propagates the Python service's real answer: the `.catch(() => null)` and the
  unconditional "idle" write are gone. 404 → NOT_FOUND, 409 (paused) → CONFLICT, 5xx → the
  service's own message, unreachable/timeout → SERVICE_UNAVAILABLE, unparseable 200 → refused.
- [x] `updateScheduleCadence` **deleted, not rebuilt.** Nothing in the runtime parses cron:
  `job_definition.schedule` is written by each lane and read by nothing, and the one periodic driver
  ticks on a fixed asyncio interval. An editable cadence box would be a control over a string no
  scheduler consults. The panel shows the value labelled as declared/documentary.
- [x] `/admin` is gated: new `src/app/admin/layout.tsx` does a server-side `platformRole === "admin"`
  check and redirects, mirroring `src/app/moderation/page.tsx`. Before this the whole admin console
  was publicly loadable and only failed query-by-query.
- [x] `JobRunnerDashboard.tsx` rebuilt: lane cards show handler, version count, declared cadence,
  slice budget/lease, pause state and the last run's real counters and error text; the "Logs
  Inspector" tab is now a genuine execution-history table of `agri.job_run` rows (lane, status,
  ok/failed/total items, requested_by, started, finished, duration, error). Toggle and Run Now
  surface success *and* failure — the invented `records_processed`, `pg_try_advisory_xact` and
  "Avg Slice Budget 300s" tiles are gone.

## Phase 4: Verification
- [x] `tests/test_inapp_scheduler.py` **deleted** — it asserted only that a hash function is
  deterministic and that a boolean flips when a task starts; neither statement touched a database.
  Replaced by `tests/test_jobs_dispatch.py` (17 tests: general dispatch across two lanes,
  unknown-lane refusal naming the known lanes, handler-token-missing refusal, the paused skip
  proving the trigger is never reached, and the route's 200/400/404/409/500 shapes including a
  driver fault whose bound parameters must not leak).
- [x] `tests/test_jobs_dispatch_agri_db.py` (3 tests) runs against a real PostgreSQL through the
  `AGRI_TEST_DATABASE_URL` / `agri_db_async_dsn` fixture: a dispatched lane opens a real
  `agri.job_run` row and drives one real work item; a paused lane runs nothing **and is still
  paused afterwards**; and pause state distinguishes an unregistered lane from a paused one.
- [x] `tests/test_strategy_mv_refresh.py` updated for the general route, plus a new test that the
  periodic driver ticks through `dispatch_lane` (so one pause switch serves both surfaces).
- [x] `src/__tests__/api/jobs-trpc.test.ts` rebuilt (15 tests): non-admin rejection on all four
  procedures, the ledger queries naming `agri.job_definition`/`agri.job_run` and never
  `job_schedules`, toggle NOT_FOUND, and the trigger propagating a Python 500 / 409-paused /
  404-unknown / timeout instead of swallowing it.
- [x] Sweep run 2026-08-14: pytest 20 passed (dispatch + real-DB) and 17 passed
  (strategy-mv-refresh); vitest 15 passed; `ruff format --check`, `ruff check` and `mypy src` clean
  on `jobs/**`; `tsc --noEmit` and `eslint` clean on the changed TypeScript.
