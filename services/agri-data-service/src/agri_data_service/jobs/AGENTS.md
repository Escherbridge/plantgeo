# Durable job execution runtime

This package is the reusable primitive every data load and backfill in this service should be built
on. Adding a durable lane is filling in two shapes — a `JobDefinitionSpec` and a `JobHandler` — exactly
as `ingest/source.py`'s `IngestionSource` made adding a source a matter of filling in a shape rather
than writing a pipeline. Three modules: `registry.py` is the declarative contract and the handler
token table, `lease.py` is the fenced-lease protocol in raw SQL, `worker.py` is the bounded run loop
plus the idempotent definition/run openers.

## Why this runs on `agri.job_*` and not a filesystem cursor

Every backfill in this repo before this package kept its progress somewhere on the container's disk or
implicitly in the shape of its arguments — a `--start-date` an operator had to remember, a checkpoint
file under `execution/local_store.py`, a shell script that walked a date range. On Railway that is the
wrong substrate three times over. A cron service is `restartPolicyType: NEVER` and gets a fresh
filesystem on every tick, so nothing on disk survives. Two containers of the same service can overlap,
and a file has no lock a second process respects. And most importantly, a file cannot answer the
question the whole workstream exists to answer: **which shards have landed?** `job_work_item.shard_key`
is a first-class, indexed, run-scoped column in the same database as `agri.*`, so "which 5-day FIRMS
windows are still missing" is a `GROUP BY` joined against the domain tables, not a script that reads a
directory.

The ledger was already deployed (PG 18.4 / PostGIS 3.6, verified 2026-08-07) and empty — every
`job_*` table had zero rows. Nothing in it is new; this package is the runtime the tables were designed
for and were waiting on.

**Every constraint on these tables is `NOT DEFERRABLE`/`IMMEDIATE`.** They fail at *statement*
execution and abort the whole transaction there, not at `COMMIT`. That single fact shapes almost every
statement in `lease.py`: you can never write a row in a temporarily inconsistent shape and repair it
later in the same transaction, so every co-dependent column pair moves in one `UPDATE`. `status`,
`lease_owner`, `lease_expires_at` and `fencing_token` always travel together; a terminal status always
travels with its `completed_at`; `retry_wait` always travels with its `next_attempt_at`.

**Every statement is `agri.`-qualified, always.** Nothing in the application sets `search_path`
(`grep -rn "SET search_path\|server_settings" src/` returns zero hits), and production reports
`"$user", public`. An unqualified `job_work_item` resolves to nothing at all.

## Why fencing tokens matter

Two workers, one expired lease. Worker A claims shard `firms:2003-04-01`, its container is killed at
minute nine of a ten-minute lease but its process lingers long enough to finish an HTTP call. The lease
expires. Worker B claims the same shard on the next tick. Now A wakes up and writes a checkpoint.

Without a fence, A's checkpoint overwrites B's cursor and the shard silently rewinds — B keeps working
forward while the ledger says it is somewhere else, and the next resume starts from a cursor that
matches nobody's actual progress. With a fence, A's claim carried `fencing_token = 7` and B's claim
bumped the item to `8`; every one of A's writes carries `AND fencing_token = 7` in its `WHERE`, matches
zero rows, and returns `False`. **A heartbeat that no longer owns the fence returns `False`, it does
not raise** — the caller's correct response is to stop working, not to crash, because nothing has gone
wrong: another worker owns the shard now and its work is theirs.

The token is derived as `job_work_item.fencing_token + 1` computed *inside* the claiming `UPDATE`,
never read-then-written. This is the only safe derivation given `uq_job_attempt_item_fence UNIQUE
(job_work_item_id, fencing_token)`:

- `attempt_count + 1` is wrong. `attempt_count` is capped by `attempt_count <= max_attempts`, so it
  saturates, while `fencing_token` is an uncapped bigint. The moment any path bumps the fence without
  charging an attempt — and `defer_work_item` is exactly such a path — `attempt_count + 1` collides
  with a token that already exists and the claim dies on the unique index.
- A global sequence would be unique but is strictly worse. The uniqueness scope is *per work item*, the
  claim already holds an exclusive row lock on that item, and `+1` on the row is transactional: a
  rolled-back claim does not burn a token, where `nextval` does. It also makes "is my token still the
  live one?" a single-column comparison against the item row.

The token is **never** reset on completion and **never** bumped by the reaper. Monotonicity is the
whole mechanism; resetting it makes a superseded worker's token recur, and bumping it in the reaper
burns a token with no attempt row behind it, breaking the invariant `fk_job_checkpoint_attempt_fence`
is built on.

### The fence guards the attempt too, and it reaches the item row to do it

`complete_work_item`, `fail_work_item` and `defer_work_item` each close the **attempt** one statement
before they write the **item**. Those three attempt closes are fenced, and the fence they carry is
`job_work_item`'s, joined in as a locked CTE — not `job_attempt`'s own column.

That distinction is the whole of it. `_OPEN_ATTEMPT` stamps the attempt row with the claim's token and
nothing ever updates that column, so `AND job_attempt.fencing_token = :fencing_token` matches its own row
forever and fences nothing at all. The column that *moves* when a shard changes hands is
`job_work_item.fencing_token`, so every fenced write has to compare against the item. And the join takes
`FOR UPDATE`: under READ COMMITTED a bare join reads a statement snapshot that may predate a competing
claim's commit, while locking the row makes PostgreSQL block on that claim, re-evaluate the predicate
against the row it committed, and match zero. The item `UPDATE` one statement later wants the same lock
regardless, so this moves the wait rather than adding one.

Without this, a worker that had lost its lease still wrote `status = 'succeeded'` onto its own attempt
row before discovering the loss. Observed against a real server: `complete_work_item` correctly returned
`False`, and inside the caller's still-open transaction the attempt already read `succeeded` — a
*terminal* verdict one layer below the item, on a shard another worker owns.

**The caller's rollback is still expected, and is now the braces rather than the whole belt.**
`worker.py::_apply_completion` routes every `False`/`None` return into `_abandon`, whose first act is
`session.rollback()`, and `_apply_park` and `_record_failure` do the same. Any future caller of `lease.py`
that commits after a refused return is still writing a transaction that had no authority — the fence now
means the attempt row inside it is unchanged rather than terminally wrong, but the correct response to a
refusal has not changed: roll back, then `release_lost_attempt`.

`_CLOSE_ATTEMPT_LOST` is the deliberate exception and must stay unfenced. It runs *precisely* when the
fence has already moved, so fencing it would match zero rows and leave behind the dangling `running`
attempt it exists to reap. It is safe unfenced because `lost` is the only verdict a superseded worker is
ever entitled to reach about its own attempt. `_EXTEND_ATTEMPT_HEARTBEAT` is likewise unfenced and safe:
`extend_lease` only reaches it after the fenced item update returned a row in the same transaction, and it
writes no status.

## Why the time budget is the durability primitive

A Railway cron container is a one-shot process. It starts, it is given no promise about how long it may
live, and `restartPolicyType: NEVER` means nothing brings it back if it dies mid-work. The only
execution model that is honest about that is: **do as much as fits in a bounded budget, checkpoint
every step, park what is unfinished, exit 0.** The next tick claims the same shards straight out of the
ledger and resumes each from its last cursor.

`run_job_slice` makes this explicit rather than implied. The budget is checked *before* claiming another
item, so a slice never starts a shard it has no time to make progress on. When the budget runs out with
a shard still in hand, the shard is **parked** (`defer_work_item` with `resume_at = now()`), not left
leased — leaving it leased would mean waiting out the full `lease_seconds` before the reaper noticed,
burning a whole tick of a `*/15` cadence for nothing.

`JobDefinitionSpec` refuses `lease_seconds <= time_budget_seconds`. A lease shorter than one slice means
a worker's own lease expires while it is still working, another worker claims behind it, and the first
worker's next checkpoint is refused by the fence — a self-inflicted fence loss on every single slice.

The handler is handed a `heartbeat` callable and a `seconds_remaining` figure so it can decide its own
step size. **The heartbeat commits.** An uncommitted heartbeat is invisible to every other connection
and therefore cannot hold off the reaper at all, so it would be theatre. A handler that shares this
session must treat every heartbeat as a commit boundary — which is what you want anyway, since durable
domain writes and durable progress belong on the same side of a commit.

## The budget-aware handler contract

`JobInvocation.seconds_remaining` is the only lever that can stop a handler starting a unit of work it
cannot finish, so it has to be worth trusting. It is the seconds left in **this slice's** budget, on the
same `time.monotonic` clock `run_job_slice` started, snapshotted when that call was built.

It is the binding constraint and the lease is not, because `run_job_slice` refuses a budget that is not
strictly shorter than the definition's `lease_seconds`. `JobDefinitionSpec` already refused
`lease_seconds <= time_budget_seconds`, but the `budget_seconds` argument overrides that value at call
time and bypassed the spec entirely, so the same rule is re-checked against the loaded definition. Only
with that check is "the lease outlives the budget" true for every path into the loop, and only then can
a handler treat `seconds_remaining` as its whole clock.

It does not tick down inside one call. The contract is **one bounded step per call**: return
`progressed(cursor, …)` and the loop checkpoints, re-invokes, and hands over a fresh figure. A handler
that wants a live clock is a handler doing too much in one call.

Two members carry it:

- `invocation.has_budget_for(seconds) -> bool` — true when the remaining budget still covers a step the
  handler estimates at `seconds`.
- `JobHandlerOutcome.yielded(cursor=…, progress_fraction=…, reason=…, metrics=…)` — "I stopped early
  because the clock is nearly gone; re-claim me."

**A yield is not a failure and never spends the retry budget.** It parks on the same `defer_work_item`
primitive a deferral uses, which raises `max_attempts` by one rather than charging it, so a shard that
straddles twenty ticks is not twenty attempts closer to `dead_letter`. It lands as `yielded` in the
slice summary, counted apart from `deferred`, because "we ran out of tick" and "upstream had nothing"
are different operational facts. Its cursor is checkpointed before the park, so the step it *did*
finish is durable.

A yielded outcome may not pin a `resume_at` — the resume time of a budget yield is "the next tick" by
definition, and a handler that means "not before T" is describing a deferral. Conflating them hides
which of the clock and upstream actually stalled.

A handler yield also ends the slice (`stop_reason: time_budget_exhausted`). Claiming another shard after
a handler has said it has no clock left only buys another yield.

The runtime keeps its own budget check at the top of the drive loop regardless, and parks the shard
itself when the deadline passes. `has_budget_for` is the handler's chance to stop *cleanly* one step
earlier; it is not what makes the budget safe.

`ingest/archive_walk.py::budget_stop_outcome` is the worked example: it estimates the next chunk from the
slowest one this window has already walked, asks `has_budget_for`, and yields with the window's running
counters as `metrics` when the answer is no. It used to return `deferred(now + seconds_remaining, …)`
because `run_job_slice` kept claiming after a park and a shard parked to `now()` respun against its own
refusal — the slice-ending yield is what removed the need for that arithmetic, and a yield now *refuses* a
`resume_at` outright.

## Why max_attempts exhaustion is `dead_letter` and never silent success

`WorkItemState` has no `failed`. A failed attempt lands the *item* in `retry_wait` (budget remaining) or
`dead_letter` (budget spent). There is a real temptation, when a shard has failed five times, to mark it
done and move on so the run can finish — resist it completely. A completeness report is a `GROUP BY`
over `shard_key`, and a dead-lettered shard must read as **missing** in that report, because it *is*
missing. A shard that quietly reports success after exhausting its retries is indistinguishable from one
that worked, which is the exact failure mode this whole ledger exists to make impossible.

`fail_work_item` decides from `claim.attempt_number >= claim.max_attempts` — the value the claim
returned, already incremented — so the test is `>=`, not `+1 >`. Dead-lettering sets `completed_at` in
the same statement as the status (`ck_job_work_item_terminal_item_has_completion_time`) and increments
`job_run.failed_work_items`. `JobRunState.partial` is what a run with some dead-lettered shards becomes:
a real, reportable outcome, and the one `job_dependency.required_status` accepts alongside `succeeded`.

## A deferral must not spend the retry budget

`defer_work_item` raises the item's `max_attempts` by one alongside setting `deferred`. This looks odd
and it is deliberate.

The claim has already incremented `attempt_count`, and a deferral means *upstream had nothing to give
yet* — that is not a failure, and charging the item's failure budget for it dead-letters a weekly source
polled hourly after five hours of behaving perfectly correctly. USDM is exactly this shape. The
alternatives were considered and rejected: decrementing `attempt_count` would make the next claim reuse
an `attempt_number` and violate `uq_job_attempt_item_number`, and leaving the budget spent makes
"waiting" indistinguishable from "broken". Raising the ceiling is legal — both
`ck_job_work_item_positive_work_item_max_attempts` and `attempt_count_within_limit` are satisfied — and
it keeps attempt numbers dense-unique.

The visible cost: a shard's `max_attempts` drifts above the definition's value once it has deferred. An
operator reading the row must know that a `max_attempts` of 9 on a definition that declares 5 means "it
has waited four times", not "someone edited it". A budget yield uses the same primitive, so a shard that
straddles many ticks accumulates the same drift. A handler that defers forever is a handler bug, and
`next_attempt_at` makes it visible.

**A park checkpoints its cursor first.** `deferred()` and `yielded()` both accept a `cursor`, and both
have it written through `record_checkpoint` before the item is parked, exactly as a completion's final
cursor is. Accepting a cursor and then discarding it would be the worst of both: a lane that walked four
of five chunks and hit an upstream "come back at 06:00" would throw those four away and re-walk them on
every deferral, forever, while the constructor's signature promised otherwise.

That is also why `_ADVANCE_CHECKPOINT_SEQUENCE` assigns
`progress_fraction = GREATEST(progress_fraction, :progress_fraction)`. A park states *where it resumes*,
not *how far it got*, so its fraction defaults to zero; assigning that raw would rewind the shard's
progress every time it waited. The `job_checkpoint` row still stores the raw per-step value — the item
column is the high-water mark.

Backoff has no jitter. Jitter would need a pinned seed to keep tests deterministic, and the failure mode
it defends against — a thundering herd — is not ours: `FOR UPDATE SKIP LOCKED` already serialises the
claim, and the topology is one worker per cron container, not a fleet waking together.

## What the runtime owns because the schema does not

The survey against production found **zero triggers** on any `job_*` table (`pg_trigger where not
tgisinternal` is empty). Unlike the release-set and strategy planes, this ledger has no server-side
enforcement beyond its CHECK constraints. Everything below is this package's responsibility, and
getting any of it wrong is silent:

- **No fencing-token allocator.** There is no sequence for jobs in `pg_sequences`; `fencing_token` is a
  plain `bigint DEFAULT 0`. `lease.py`'s claim `UPDATE` is the allocator.
- **No lease-reclaim mechanism.** `ix_job_work_item_lease_expiry` exists to support one and nothing used
  it. `reclaim_expired_leases` is it. It reclaims to `retry_wait`, never to `queued`: `queued` with a
  stale non-NULL lease pair passes every CHECK and produces a row that lies about who owns it, while
  `retry_wait` *forces* `next_attempt_at`.
- **Nothing reaps an orphaned attempt.** There is no FK, CHECK or trigger connecting
  `job_attempt.status` to `job_work_item.status` — you can have five `running` attempts on one
  `succeeded` item and the database is happy. `release_lost_attempt` and the reaper's `close_lost_attempts`
  are what stop an abandoned attempt sitting in `running` forever and poisoning every incident query
  that counts live work.
- **The claim index does not serve the claim, and the `next_attempt_at` seeding is a convenience.**
  `ix_job_work_item_claim` is `(status, next_attempt_at, available_at, priority)`. It leads with neither
  `job_run_id` — which `_CLAIM_WORK_ITEM` filters on — nor `priority DESC` — which it sorts by — so the
  claim is a filter-and-sort whatever the rows look like. `open_job_run` still seeds
  `next_attempt_at = available_at` at fan-out (legal: the CHECK requires it only for
  `retry_wait`/`deferred` and forbids it nowhere) so that every row carries the same claim-eligibility
  shape, but nothing in this runtime depends on it: the claim predicate spells the NULL case out as
  `(item.next_attempt_at IS NULL OR item.next_attempt_at <= now())`. The seeding matters only to a query
  written *without* that disjunction — an operator's ad-hoc completeness check, or a future claim
  variant — where a NULL would make the comparison UNKNOWN and hide the row. **Do not quote the seeding
  as load-bearing; it is not.** (An earlier revision of this file and of `worker.py` claimed an unseeded
  row was invisible to the claim itself. It was wrong, and the tests repeated it.)
- **No run-counter maintenance.** `total/succeeded/failed_work_items` are hand-maintained under a hard
  `succeeded + failed <= total` CHECK. `refresh_job_run_rollup` assigns all three absolute values
  recomputed from the work items in **one** statement, which is the only shape that cannot transiently
  violate the sum. The incremental `+1` bumps in `complete_work_item`/`fail_work_item` are a crash-safe
  approximation kept for the case where a slice dies before its rollup; they are clamped with
  `GREATEST(current, LEAST(current + n, total - other))` so a stale total can never abort a completion
  that actually happened. The rollup is the authority.
- **No concurrency-key enforcement.** `job_definition.concurrency_key` and `queue_name` are plain
  strings with no index and no advisory-lock machinery. Per-key serialisation is still unbuilt. Today
  the fence makes concurrent workers *safe*; it does not make them *excluded*.
- **`job_event` is partitioned `RANGE (occurred_at)` with exactly one partition (`job_event_default`)
  and no partition manager.** This runtime deliberately writes no `job_event` rows: every row would land
  in the default partition and nothing prunes it. Operational telemetry goes to structlog on stderr
  until a partition manager exists. See `models/AGENTS.md` §job_event.

## Sessions, transactions and who commits

`lease.py` never commits. `worker.py` owns every transaction boundary, because the boundaries are
load-bearing and belong where the loop is:

1. **Claim commits immediately.** `FOR UPDATE SKIP LOCKED` holds the row lock until commit; holding it
   for the whole handler would keep a transaction open for minutes and make the heartbeat pointless.
2. **Each checkpoint is its own transaction.** That is what "checkpointed" means.
3. **Completion/failure/deferral is its own transaction**, ordered attempt → item → run counter, so a
   crash between statements leaves the shard still leased and re-drivable rather than leaving a
   completed shard behind a `running` attempt.

`ingest_session()` (`db/engine.py:133`) creates a brand-new engine per invocation and disposes it in
`finally` — one connection, one engine, per `async with`. **Never open it per work item**; that is a
full TCP+TLS+auth handshake against the Railway proxy per shard. Open it once around
`run_job_slice`. `expire_on_commit=False` means ORM attributes are stale after commit, which is one more
reason this package reads every value back through `RETURNING` and never off a cached instance.

Every timestamp comes from the **database** (`now()`), never from the worker's clock. Two workers with
skewed clocks would otherwise disagree about whether a lease has expired, which is the one disagreement
a lease protocol cannot survive. Python computes only intervals (`lease_seconds`, `backoff_seconds`) and
the slice budget, and the budget uses `time.monotonic` so a clock adjustment mid-slice cannot extend or
truncate it.

A transaction-local `statement_timeout` of 120s is pinned at the start of every transaction
(`apply_statement_timeout`), matching the repo-wide CLI/procedure convention. `_commit`/`_reset` re-arm
it, because `SET LOCAL` dies with its transaction.

### The handler shares this session, so the runtime may not trust what it left behind

A handler is handed the slice's own `AsyncSession` — `ArchiveWalkContext.write_features` binds a feature
writer over it — and `ingest/writer.py` both **commits** that session per batch and **rolls it back** on
error. Two consequences the runtime has to absorb, because `writer.py` is outside this package and
cannot be asked to hold the invariant:

1. **The statement timeout is gone after the handler's first commit.** `SET LOCAL` dies with its
   transaction and `_ingest_resolved_batch` re-arms nothing, so from the first written batch onward the
   runtime's own ledger statements would run under the server default. The runtime therefore re-pins the
   timeout at the two places control comes back from handler code: at the top of `_LeaseGuard.heartbeat`,
   and in `_invoke_handler` immediately after the handler returns. Every other ledger write is reached
   through a `_commit` or `_reset`, both of which already re-arm.
2. **The transaction may be aborted even though the handler looks fine.** `writer.py` calls
   `resolve_layer_id` *outside* `_ingest_resolved_batch`'s try/except-rollback, so a connection reset, a
   lock timeout or the 120s statement timeout there leaves the session in `InFailedSQLTransaction`, in
   which every subsequent statement raises. **`_fail_after_error` therefore opens with `_reset`,
   unconditionally, exactly as `_abandon` does.** Without it, closing the attempt on the aborted session
   raises a *second* exception, that one escapes `run_job_slice` — the module has exactly one `except` —
   the container dies, and the work item is stranded `status='running'` behind a live lease the next
   cron tick's reaper may not touch until it expires. `refresh_job_run_rollup` never runs either, so the
   counters stay stale. This is the FIRMS failure in a new costume: a shard that is neither done nor
   visibly failed.

For the same reason, `_invoke_handler`'s `try` covers **persisting the outcome as well as producing
it**. Both run against the session the handler just used, so both can inherit an aborted transaction;
covering only the handler call would leave the second half of the same hazard open.

Rolling back in `_fail_after_error` also discards whatever the handler wrote and never committed. That
is correct, not a loss: no checkpoint recorded it, so the next claim re-walks that work regardless, and
committing it *alongside* a failure would be the one thing this ledger exists to prevent — data landing
that the ledger cannot account for.

## Redaction

Redaction lives at the **chokepoint**, not at the call sites. `fail_work_item` and `defer_work_item` run
every free-text field they are given through `clamp_summary` (`redact_text` then `clamp_text`) before it
becomes a durable `last_error_summary`, `error_summary` or `deferral_reason`. Redacting first and
clamping second is deliberate: clamping first can cut a URL short and leave the key in the half that
survived.

This is belt and braces on purpose. Those two functions are reached by two different kinds of string —
an exception, which has already been through `failure_summary`, and a **handler outcome's free-text
`reason`, which has been through nothing at all**. An earlier revision applied only `clamp_text` on the
outcome path, so the guarantee rested entirely on every future handler happening to write a safe reason.
It should rest on the code.

`failure_summary` remains the right thing for an exception: it degrades a `SQLAlchemyError` to its class
name, because the SQLAlchemy message carries the whole statement and its bound parameters, and otherwise
delegates to `clamp_summary`. `failure_class` is redacted and clamped to 255 characters because that is
its column width and an over-long value aborts the write rather than truncating, which would turn "this
shard failed" into "this slice crashed".

`_SECRET_SHAPED` substitutes a whole whitespace-delimited token per match — scheme-shaped
(`https://…`), `user@host`-shaped, or a bare `?query` tail — rather than parsing the URL. Partial
matching is what lets a secret survive. The query-tail alternative exists because a message can name a
key without naming its scheme.

**`ingest/results.py::failure_reason` is deliberately the same code, duplicated, and the two must change
together.** Its docstring had claimed to redact since it was written and performed no substitution at
all, so an `httpx.HTTPStatusError` carrying a FIRMS URL — the API key is in the *path*,
`/api/area/csv/<MAP_KEY>/…`, not in a query string — passed through verbatim into the cron log. It
redacts now. It is not imported from here and does not import from here: `jobs` is the reusable
primitive `ingest` builds on, and a dependency in either direction would invert that layering for a
twelve-token regex. Both copies carry a comment naming the other.

## Metrics

`job_attempt.metrics` is written on **every** attempt-closing path, not just the successful one.
`JobHandlerOutcome.metrics` is accepted by all five outcome constructors, and the drive loop accumulates
it across a shard's handler calls — key-wise, newest call winning, which is why a handler reports
cumulative figures rather than per-step deltas — so a step that only `progressed` still has its counters
land when the attempt finally closes as succeeded, failed, deferred or yielded.

The failed path matters most. What a dead-lettered window actually managed — chunks walked, records
seen, records written, whether it hit a record cap — is exactly what an operator needs to tell "upstream
had nothing" from "we never reached upstream", and it is unanswerable from the ledger alone if the
failure path drops it. The column is `NOT NULL DEFAULT '{}'`, so an empty object is always legal.

The one path that still writes no metrics is `close_attempt_lost`: a fenced-out attempt's counters
describe work whose durability now belongs to another worker, and reporting them would invite reading
them as this shard's progress.

## Known deviation: the SQL is inline, not extracted

`conductor/code_styleguides/sql.md` §"Runtime query SQL lives in dedicated files" requires a non-trivial
runtime query to live in `src/agri_data_service/sql/<package>/<name>.sql`, loaded through
`agri_data_service.db.sql_queries.load_query_sql`. **Neither that directory nor that loader exists yet**
— `src/agri_data_service/sql/` is absent and `db/sql_queries.py` is absent (only `db/sql_objects.py`, the
*declarative* loader, exists). Creating the loader means editing `db/`, which is outside this package's
file boundary. The statements here are therefore inline `text()` constants, each opening with a
`-- <statement_name>` marker comment that serves as both documentation and the handle the unit tests
match on. **Extracting them to `sql/jobs/*.sql` is the follow-up**, and it is mechanical: the constant
names already map one-to-one onto the file names the styleguide prescribes. Note when doing it that
SQLAlchemy's `text()` bind-parameter regex matches `:word` anywhere in the string, comments included, so
no comment in these statements may contain a colon.

## The DBOS Transact evaluation, and why we did not adopt it

Recorded so it is not re-litigated. DBOS Transact (Python) was evaluated as an alternative to writing
this runtime, against upstream source pulled from `main` (captured under `.omc/research/dbos/`).

What it genuinely offers: `@DBOS.workflow` fully supports `async def` — it is not a sync-only library.
Its recovery machinery is real. And `SetWorkflowAttributes` stores GIN-indexed JSONB on
`workflow_status`, which is a legitimately good queryable index over workflows.

Why it is nonetheless the wrong tool *here* — our deployment violates three of its four load-bearing
assumptions:

1. **It assumes a long-lived process drains recovered work.** Recovery is `launch()`-scoped: it scans
   for PENDING workflows and *re-enqueues* them onto a queue drained by polling threads. Our containers
   are `restartPolicyType: NEVER` one-shots that exit before that round trip lands. Worse, recovery is
   gated on `app_version` (a hash of registered function source), so any deploy touching workflow code
   strands every in-flight backfill until someone manually resumes it — and our backfills change often.
   `DEFAULT_MAX_RECOVERY_ATTEMPTS = 100` burns one attempt per cron start, so a workflow that cannot
   finish inside one window is dead in about a day with no domain-level record of why.
2. **It assumes the app can afford a sync engine.** Its system database is always
   `sa.create_engine(...drivername="postgresql+psycopg")` — synchronous psycopg3, regardless of the
   app's async-ness, with async call sites pushing every checkpoint through `asyncio.to_thread`. We are
   `sqlalchemy[asyncio]` + asyncpg end to end, with `config.py` hard-validating the
   `postgresql+asyncpg` scheme on every DSN. Adopting it means a third Postgres driver, a second pool,
   a thread pool and a background event loop.
3. **It assumes durable execution owns its own DDL.** `_launch()` will `CREATE DATABASE`,
   `CREATE SCHEMA` and run `dbos_migrations` — and, on a 30-second advisory-lock timeout, proceeds
   *without* the lock. Against a repo where Alembic is the only component permitted to touch the `agri`
   schema, that is a second uncontrolled DDL actor.
4. **Its headline value is not our problem.** Exactly-once side effects is what DBOS sells. Our history
   chunks are deterministic (anchored window start, fixed step) and the feature writer's diff rejects an
   unchanged payload, so replay is already free.

And it does not deliver what we actually needed. Step outputs land in `operation_outputs.output`, a
`Text` column holding base64'd pickle keyed by `(workflow_uuid, function_id)` — structurally
unqueryable, so "which windows landed" can never be a SQL predicate over them. The attributes path gets
there only by inflating every 5-day window into a workflow row in a *different logical database*, so the
completeness report could never be a plain join against `agri.*`. And it has no time-budget primitive at
all: its only time control is a timeout that `CANCEL`s the workflow and requires a manual
`resume_workflow`, which would mean reimplementing `next_attempt_at` on top of the thing we adopted to
avoid writing it. `agri.job_definition.time_budget_seconds` already does the intended thing natively.

**Revisit only if the deployment model changes to a long-lived worker fleet**, at which point re-evaluate
honestly — the async support and the attribute index are real.

## Tests

`tests/test_jobs_lease.py`, `test_jobs_worker.py` and `test_jobs_registry.py` are pure unit tests with no
database, matching the ingest lane's convention (`test_ingest_backfill.py`: *"no database, fakes for both
seams"*). The seam is `AsyncSession.execute`: each test file carries a `RecordingSession` that records
`(sql, parameters)` and answers a statement by the `-- <statement_name>` marker it contains. That makes
the assertions readable — a test names the statement it means — and it pins the decision logic (budget
exhaustion, fence loss on heartbeat and on checkpoint, the backoff schedule, `max_attempts` →
`dead_letter`, expired-lease reclaim, idempotent run opening) without a connection.

What these tests do **not** prove, and what a real-DB pass would: that the SQL parses, that the
constraint names cited in the comments are the ones production actually carries, and that
`jsonb_to_recordset`'s column list matches the JSON `_work_items_json` emits. `tests/conftest.py` already
provides `agri_db_connection` gated on `AGRI_TEST_DATABASE_URL`; a `test_jobs_lease_postgresql.py` that
drives the real protocol against a disposable database is the outstanding follow-up.
