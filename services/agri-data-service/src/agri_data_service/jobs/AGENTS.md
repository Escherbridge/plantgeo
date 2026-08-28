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

### …but the protection is bounded, at `MAX_CONSECUTIVE_PARKS`

Nothing bounded it originally, and unbounded is wrong in one specific shape: a shard that parks on
*every* tick without ever advancing. `jobs-run --lane firms-archive --budget-seconds 600` run once
against a window that has already measured a 350s chunk mints exactly that — `budget_stop_outcome` yields
before every chunk, the window never advances, never spends an attempt, never dead-letters, `max_attempts`
climbs without limit, `jobs-run` exits 0 and `jobs-status` reports it as a healthy `deferred` forever.
The answer to "is a poison item guaranteed to stop retrying?" was yes for a *failing* item and no for a
*parking* one.

`_DEFER_WORK_ITEM` therefore raises the ceiling only while the shard's **consecutive** park count is
under `MAX_CONSECUTIVE_PARKS` (24 — half a day of the deployed 30-minute cadence). Past that the ceiling
stops rising while the claim keeps charging `attempt_count`, so the budget closes and the shard finally
dead-letters into a report that says it is *missing*, which is the honest answer.

There is **no park-count column and this runtime adds none.** The count is derived inside the same
statement: `job_attempt` rows for this item with `status = 'deferred'` whose `fencing_token` is newer than
the newest `job_checkpoint`'s. That makes it consecutive-since-progress rather than cumulative, which is
the distinction that matters — a window that walks a chunk and *then* yields for the clock checkpointed
under this very token, so its count is zero and it is never penalised for taking many ticks. Only a park
that recorded nothing counts. `close_attempt_deferred` has already run when the item `UPDATE` fires, so a
park is included in its own count.

If a `parked_count` column is ever added, this derivation is what it should replace.

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
  `retry_wait` *forces* `next_attempt_at`. **A slice scopes it to the DEFINITION, not to the run it
  drives.** `_select_open_job_run` picks exactly one run per tick — the oldest open one — while a lane
  mints a second run every time its floor is lowered, because the floor is part of `logical_run_key`.
  Scoped to the driven run, a shard stranded behind a dead lease in a sibling run was reclaimable by no
  tick at all and sat there until someone hand-`UPDATE`d it. The `job_run_id` scope still exists and is
  what an operator or a test drives directly; the two are ANDed, and only non-terminal sibling runs are
  in scope, since a terminal run reached its status by having every shard settled.
- **Nothing reaps an orphaned attempt.** There is no FK, CHECK or trigger connecting
  `job_attempt.status` to `job_work_item.status` — you can have five `running` attempts on one
  `succeeded` item and the database is happy. `release_lost_attempt` and the reaper's `close_lost_attempts`
  are what stop an abandoned attempt sitting in `running` forever and poisoning every incident query
  that counts live work. **So is `_CLOSE_SUPERSEDED_ATTEMPTS`, and it closes the hole the other two
  cannot reach**: the claim's expired-lease arm supersedes an attempt without the superseded worker
  participating at all, `release_lost_attempt` runs on that worker (which in the crash case is the
  process that died), and `close_lost_attempts` is bound only to the items the reaper reclaimed in its
  own single pass at the top of the slice. Any lease that expires *after* that pass and is then taken by
  the claim leaked its attempt permanently. The claim now closes it as `lost` in the same transaction,
  under the item lock it already holds. Unfenced for the same reason `_CLOSE_ATTEMPT_LOST` is — it runs
  precisely when the fence has moved — and bounded instead by `fencing_token < :fencing_token`, so it can
  only ever reach *strictly* superseded attempts and never the one the claim is about to open.
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
- **`job_event` is partitioned `RANGE (occurred_at)`, and a partition manager now exists.**
  `db/maintenance.py::maintain_job_event_partitions` creates hot daily partitions, drains
  `job_event_default` and prunes expired ones, with the `job-logs-maintain` CLI verb in front of it. The
  old rule here — *this runtime deliberately writes no `job_event` rows until a partition manager
  exists* — is therefore retired. **The runtime writes exactly one row per tick** (see "Shutdown and
  heartbeat semantics" below) and nothing else; per-chunk detail still goes to structlog on stderr,
  because that is genuinely high-volume and genuinely disposable. `job_incident` and `job_outbox` are
  still written by nothing. See `models/AGENTS.md` §job_event.

## Choosing a `logical_run_key`: when bucketing strands a shard

`strategy_mv_refresh.py::_run_bucket_key` mints a NEW `job_run_id` every
`STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS` by rounding the trigger moment down to that interval and
folding it into the run key. This is a real pattern with a real purpose -- it is what lets a periodic
tick and a manual `POST /jobs/trigger` landing seconds apart collapse into ONE run via
`open_job_run`'s `ON CONFLICT (logical_run_key) DO NOTHING` -- but it has a failure mode that only
shows up under a crash, and production has already produced one: `.omc/RUNBOOK.md`'s "Traps
discovered" section records a `strategy-mv-refresh` work item sitting `retry_wait` under a run stuck
`running` from `20260815T010000Z`, unreachable by any later tick.

**The mechanism.** `run_job_slice` is told which run to drive via `job_run_id`, and
`claim_work_item` is scoped to that exact `job_run_id` -- it can never see a work item that belongs
to a different run. `reclaim_expired_leases`, by contrast, is scoped to `job_definition_id` (see "No
lease-reclaim mechanism" above) precisely so a reaper can reach a shard in a sibling run. Those two
scopes being different is fine as long as something eventually calls `claim_work_item` against THAT
sibling run again -- and a bucketed key guarantees nothing ever will. Once the bucket that opened a
run has closed, no future `_run_bucket_key(now())` ever produces that run's key again, so no future
`open_job_run` call reopens it and no future `run_job_slice` call is ever handed its `job_run_id`.
Reclaiming the lease moves the stranded item back to `retry_wait`, `retry_wait` is claimable in
principle, and it is never claimed by anything, forever.

**Bucket a run key only when the SHARDS THEMSELVES are naturally windowed** -- `strategy_mv_refresh`
fans out one work item covering all three views per poll window, so a stuck window really is stale
information the next window supersedes; the item's own staleness is what the bucket is for. **Do not
bucket a run key merely to get a fresh, claimable work item on every tick** for a lane whose shards
are NOT windowed data. `jobs/matview_refresh.py` needed exactly that (a fresh shard every tick, to
refresh whatever has drifted stale since the last one) without wanting a new run every tick, and
solved it the other way around: ONE constant `logical_run_key` reused forever
(`MATVIEW_REFRESH_RUN_KEY`), with a freshly-keyed `JobWorkItemSpec` added to that SAME run on every
trigger call. A shard stuck behind a dead lease from tick N is still inside the run tick N+1 claims
from, so the definition-scoped reaper in step 1 of `run_job_slice` can actually reach it, and the
orphan class above cannot occur by construction.

## Shutdown and heartbeat semantics

Two things every operator of this runtime has to know, because both change what a row in the ledger means.

### A SIGTERM releases the lease

`shutdown_signal()` binds SIGTERM and SIGINT to a stop flag for the length of one slice, and
`ingest/commands.py::run_archive_definition_slice` installs it — at the process boundary, because that is the only
scope that knows this is a one-shot container rather than a library call. `run_job_slice` reads the flag
in two places: before claiming another shard, and between two handler steps of the shard it already
holds. On the second, the shard is **released** on the same fenced `defer_work_item` path a budget yield
uses (`_release_in_hand`), so it is immediately claimable, costs no retry budget, and the slice ends with
`stop_reason: shutdown_requested` and a `released` count in its summary.

`JobInvocation.shutdown_requested` exposes that same flag to a handler which owns a genuinely long
external operation. The unified executor's subprocess handler polls it alongside process exit, timeout,
and lease heartbeat, then terminates (and boundedly kills) the child before yielding its checkpoint. Most
handlers should still return one small unit at a time and let the drive loop observe shutdown between
calls; the callback exists so a two-hour child process does not make that boundary unreachable.

`jobs-pulse` is itself such a child under the unified executor. It installs one `shutdown_signal()` at
its process boundary and threads the resulting flag through `dispatch_lane`, both matview triggers, and
`run_archive_definition_slice`; the archive helper reuses an existing flag instead of replacing signal
handlers inside the nested call. Thus the parent's SIGTERM reaches the inner fenced shard and gives
`run_job_slice` a chance to release at its next transaction-safe boundary before the bounded kill
fallback; it does not cancel a handler already inside one database or HTTP operation.

What this buys: before it, a SIGTERM had no Python-level handler at all, so the container simply ended
and left its shard `status = 'running'` behind a lease of up to `lease_seconds` — 2400s on the archive
lanes against a 30-minute cron. The next tick could neither claim it (`lease_expires_at <= now()` is
false) nor reap it (`lease_expires_at < now()` is false); it worked a different window and exited 0
looking healthy, and only the tick *after* that recovered the shard. Up to an hour of a lane's frontier,
lost per redeploy or per container eviction, with nothing recording that it happened.

What it does **not** buy by default: ordinary handlers still read the flag between units of work, not
inside one. A SIGTERM that lands mid-chunk is followed by SIGKILL unless that handler deliberately polls
`shutdown_requested`, and that case still relies on the reaper. Signals are *not* wired to
`task.cancel()` — cancelling a handler mid-write is a worse trade than waiting for a transaction-safe
boundary or an external-process handler terminating its own child.

`asyncio.CancelledError` is handled for the same reason and lands in the same place. It derives from
`BaseException`, so `_invoke_handler`'s `except Exception` never saw it and an externally-cancelled slice
unwound leaving exactly the stranded-`running` shard above. It now releases the shard first and then
**re-raises**: a swallowed cancellation is worse than the leak it would fix.

### The heartbeat is one `job_event` row per tick

Every return path of `run_job_slice` writes exactly one `agri.job_event` row,
`event_code = 'slice_finished'`, in the same transaction as the closing rollup. `progress` carries the
slice summary verbatim — the same object the cron log line carries — and `detail` carries a queue-depth
snapshot the INSERT computes for itself, so the heartbeat stays a single statement. Severity is `info`,
or `warning` when the tick dead-lettered something.

**`max(occurred_at) WHERE event_code = 'slice_finished'` is the lane's liveness signal, and it is the
only honest one.** A tick that claims nothing writes nothing else at all: `no_open_run` rolls back,
`no_claimable_work` breaks, and the rollup's counters do not move when nothing happened. `updated_at`
cannot substitute — it is ORM `onupdate` only, every runtime write is a raw `text()` UPDATE, and there
are no triggers, so it is frozen at insert time on both `job_work_item` and `job_run`. Do not reach for
it as a last-touched axis anywhere. `max(job_attempt.started_at)` is a fallback that reads "dead" for a
lane that is merely finished.

The no-open-run tick is deliberately the one that still writes: it is emitted identically by a lane that
finished, a lane whose windows were never fanned out and a lane whose definition name drifted, and
without a durable row all three also read the same as a cron container that never started.

Retention is `db/maintenance.py`'s job (`job-logs-maintain`), which creates the hot daily partitions and
prunes past its window. A row written when no hot partition exists lands in `job_event_default` and is
picked up by the next maintenance pass, so a heartbeat is never lost for want of a partition.

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

### `failure_summary` names the real condition now, not the dialect's tautology

Production measured `agri.job_attempt.error_summary` reading the literal `'job step failed
(ProgrammingError)'` for every one of a day's worth of `matview-refresh` and `strategy-mv-refresh`
failures, whose real cause was `UndefinedTable` on `agri.matview_refresh_state` before its migration
had been applied. `error.__class__.__name__` alone could never have said that: asyncpg's dialect
wraps the driver condition in its own `ProgrammingError`, so `type(error).__name__` reports the
wrapper, not the condition underneath it.

`failure_condition_name` fixes this by reusing the exact unwrap `routes/ops.py::_panel_error_summary`
already proved safe for an unauthenticated page: read `.orig` one level (the dialect's addition, no
more), then that value's `__cause__` (the real condition), and report only `type(x).__name__` pairs —
never `str()`, `repr()`, `.statement`, `.params`, `.detail` or `.args`, so nothing it returns can carry
a table name, a row value or a bound parameter. `failure_summary` now renders `"job step failed
(ProgrammingError: UndefinedTable)"` for exactly this case. `failure_class`
(`worker.py::_fail_after_error`) is left as the bare `type(error).__name__` deliberately — only
`error_summary` carries the richer pair, and it still funnels through `clamp_summary` at
`fail_work_item`/`defer_work_item` before it becomes durable, exactly as before.

**`routes/ops.py::_panel_error_summary` is not owned by this package and still carries its own copy of
this logic**, predating `failure_condition_name` by the incident that motivated extracting it here.
Repointing that call site at `agri_data_service.jobs.lease.failure_condition_name` is an outstanding
follow-up for whoever owns `routes/`; it is not done here because `routes/ops.py` is out of scope for
this package's ownership boundary.

## Preflight: refusing before the retry budget is spent

A lane's handler can depend on a relation existing — `agri.matview_refresh_state` is the shared
ledger both `matview_refresh.py` and `strategy_mv_refresh.py` read and write through — and until that
relation's migration lands, EVERY attempt against it raises. Left alone, that shape burns a lane's
full `max_attempts` on every single tick, forever, because both lanes mint a brand-new, freshly-keyed
work item on every trigger call (`matview_refresh.py`'s one persistent run gets a new shard per tick;
`strategy_mv_refresh.py` buckets a new run per poll window) — so there is always a fresh shard to
burn the budget on again. Measured in production: ten `matview-refresh` shards dead-lettered and
thirteen `strategy-mv-refresh` ones sitting `retry_wait`, across roughly a day, before anyone noticed
`agri.job_attempt.error_summary` said anything at all.

`worker.py::preflight_required_relations` is the fix: a lane calls it FIRST, before
`ensure_job_definition`/`open_job_run`, over the relations its handler cannot function without
(`lease.py::find_missing_relations`, a single `to_regclass` catalog lookup per name, no lock, no
error on an absent relation). Missing anything, it refuses immediately — no run opened, no work item
minted, no attempt claimed, nothing to retry or dead-letter — and returns a terminal
`JobSliceSummary` with `stop_reason = "preflight_missing_relations"` and the missing relations named
plainly in `preflight_missing_relations`.

**The refusal is not a silent early return.** It still writes the one `job_event` heartbeat row every
other stop path writes, at severity `error` (louder than a dead-lettered tick's `warning`, because
this is the whole lane refusing, not one shard failing), so `max(occurred_at) WHERE event_code =
'slice_finished'` keeps reading as "this lane is ticking" and the missing relations can be read
straight off `job_event.progress` without a second lookup, statement, or SSH session.
`docs/layer-lane-standard.md` §0's rule — "a lane that reports success having written nothing is
worse than a lane that fails" — is exactly what a refusal that looked like `no_claimable_work` would
violate; `preflight_missing_relations` is its own stop reason for precisely that reason, not reused
from `no_open_run`.

**Scoped to the relation that has no graceful path, not to every relation a lane touches.**
`matview_refresh.py`'s eleven views already handle an individual missing view gracefully
(`_view_exists` -> `skipped_missing`, self-healing the moment the relation appears — see that
module's own "Graceful skip, not a crash" comment), and `strategy_mv_refresh.py`'s three guardrailed
views do the same. Preflighting those too would turn "eight of eleven views are ready, refresh them"
into "refuse the whole tick because three views are not," a real behavioural regression this change
does not make. `agri.matview_refresh_state` is different: nothing catches its absence, so it is the
one relation both lanes declare in `MATVIEW_REFRESH_REQUIRED_RELATIONS` (`jobs/matview_refresh.py`),
and `strategy_mv_refresh.py` imports that same tuple rather than restating the literal, because it
writes to the identical table through the identical failure mode.

**Dead-lettered shards from before the fix are not re-armed, on purpose.** Both lanes' shards are
ephemeral refresh cycles, not irreplaceable data windows: `matview_refresh.py` mints a fresh,
uniquely-keyed shard into its one persistent run on every trigger call, and `strategy_mv_refresh.py`
mints a fresh shard into a fresh bucketed run every poll window, regardless of what any earlier shard
did. The moment the missing relation exists again, the very next tick's fresh shard succeeds and
refreshes every view exactly as if the outage had not happened — there is no data loss to recover,
because a matview refresh has no "window" a superseded attempt could have uniquely captured. Re-arming
old dead letters would only matter for a lane whose shards each own an irreplaceable slice of
history — an archive walk's 5-day FIRMS window, for instance — where a dead-lettered shard really does
mean a permanent gap `next_attempt_at` can never revisit on its own. Neither refresh lane has that
shape, so the ten and thirteen stale rows stay exactly what this file's "Why max_attempts exhaustion
is `dead_letter` and never silent success" already says they should be: a truthful record that a
specific tick failed, not a gap anything downstream is missing data because of.

## The matview-refresh lane's four self-inflicted stalls

All four were found by measuring production on 2026-08-17 rather than by reading the code, and all
share one shape: a view that cannot make progress is indistinguishable, to some gate, from a view that
is fine. The numbers are here because the code that acts on them is terse by house convention, and
because a first pass got several of them wrong — every figure below is a live read.

The ledger, `agri.matview_refresh_state`:

| view | `refreshed_at` | `duration_ms` | cap | outcome |
|---|---|---|---|---|
| `mv_signal_cell_daily` | 08-16 16:40 | **1,729,192** | 1,800 s → **1,900** | refreshed (96%) |
| `mv_feature_observation_day` | 08-17 10:02 | **300,257** | 300 s → **900** | **failed** |
| `mv_layer_feature_stats` | 08-17 11:49 | 42,589 | 60 s → **120** | refreshed (71%) |
| `mv_layer_hourly_activity` | 08-17 12:11 | 7,865 | 60 s → **120** | refreshed |
| `mv_signal_observation_day` | **NULL** | 300,238 | 300 s | failed (timeout, to the ms) |
| `mv_soil_survey_grid` | **NULL** | 86,320 | 300 s | failed |
| `mv_soil_survey_union` | **NULL** | 104,269 | 300 s | failed (`relispopulated=false`) |

`agri.job_attempt` 48 h: 47 failed / 7 deferred / **2 succeeded**; 15 work items standing in
`dead_letter`. Total guaranteed-doomed REFRESH per hourly tick: **791 s**.

### 1. A view that can never succeed was eligible on every tick, forever — by two different routes

`upsert_matview_refresh_state.sql` COALESCEs a failed attempt's NULL `refreshed_at` onto the stored
value, so a failure never erases a real prior success. Correct — and it makes "never succeeded" and
"never attempted" the same row. `_eligibility`'s first branch read that row as *never refreshed, try
it*.

**There is a second door, and missing it undercounted the waste by 43%.** `mv_feature_observation_day`
HAS a prior success, so its `refreshed_at` is non-NULL and it never took that branch — it re-enters
every tick through the **watermark** gate, because its watermark is `max(updated_at)` over
`geo.features` and that moves on every ingest. Both doors lead to the same unbounded loop. The backoff
sits in front of the whole gate, so it covers both; only the *census* of the damage was wrong.

`_backoff_seconds` doubles from the spec's `min_interval_seconds`, capped at its own
`max_staleness_seconds`.

#### Why the cap is `max_staleness_seconds` and not a tuning knob

That value already declares "this view must be re-attempted at least this often", so capping there is
what stops the backoff becoming silence: a view someone fixes becomes eligible on its own, with no
operator action and no second switch to remember. **But read the actual numbers before assuming that
is quick.** For the two soil views `max_staleness_seconds` is **604,800 s = 7 days**, so the schedule
is 6 h → 12 h → 24 h → 48 h → … → 7 days, and after a handful of failures a fixed view waits up to a
week to prove it. There is no in-lane verb to shorten that. The operator action is one statement:

```sql
UPDATE agri.matview_refresh_state
   SET consecutive_failures = 0
 WHERE view_name = 'geo.mv_soil_survey_union';
```

which makes the very next tick attempt it. Do that after fixing a view rather than waiting.

#### Backoff must not buy silence

The counter suppresses the WORK and never the SIGNAL. A withheld view is reported `deferred_failing`,
`MatviewRefreshReport.has_failures` returns True for it, the handler returns `failed`, the work item
still dead-letters, and `execution/jobs_pulse_command.py`'s dead-lettered-work-item census still reds
the hourly cron — the signal RUNBOOK §3 makes binding, unchanged in both directions. Producing that
signal now costs ~0 s instead of 791 s.

#### A yield used to swallow the failure signal

The paragraph above was **false as first written**, and the bug was one line of ordering. The handler
tested `budget_exhausted` *before* `report.has_failures` and returned `JobHandlerOutcome.yielded`,
which `worker.py::_apply_park` treats as a park — max_attempts raised rather than spent, so the item
never fails, never dead-letters, and the census sees nothing. Every budget-exhausted tick was therefore
**green over permanently-failing views**, for up to `MAX_CONSECUTIVE_PARKS = 24` consecutive hours.
Stall 3 below made it materially more reachable, by letting the 1,729 s view start at all.

`has_failures` is now tested first. Nothing is lost by preferring the failure: the yield cursor is an
optimisation, not a correctness device — a view refreshed earlier in the tick has its watermark and
`refreshed_at` committed, so the next tick's fresh plan skips it as `skipped_unchanged` regardless.

#### Two clocks, and which one governs what

The gate compares a Python `datetime.now(UTC)` against `last_attempt_at`, which the upsert writes from
the **database** `now()`. That mix is deliberate and was already the case for `refreshed_at`; the
thresholds are minutes-to-days, so seconds of skew cost scheduling precision and not correctness
(unlike a lease deadline — see "every timestamp comes from the database"). Two consequences worth
knowing: a container with a badly wrong clock backs off by the wrong amount, and **an OOM-killed or
disconnected refresh writes no ledger row at all**, so `consecutive_failures` does not increment for
it — that failure mode is invisible to the backoff and surfaces only as a dead-lettered work item.

### 2. The real discriminator is `Parallel Hash`, not parallelism

`dynamic_shared_memory_type = posix`, so every parallel plan allocates its segment in `/dev/shm` —
tmpfs, which is RAM, inside the 3 GB cgroup. Reproducing `mv_soil_survey_grid`'s defining query
read-only raised `could not resize shared memory segment ... to 16777216 bytes: No space left on
device` after 55.05 s; the same query at `max_parallel_workers_per_gather = 0` ran past 280 s clean.

**A default of 1 removes no exposure, and must not be read as a mitigation.** Plain `EXPLAIN` on prod:

| relation | plan | DSM shape |
|---|---|---|
| `mv_feature_observation_day_axis` | `HashAggregate` over `Gather`, Workers Planned 2 | fixed ~64 kB tuple queues |
| `mv_feature_observation_day` | `GroupAggregate` over `Gather Merge`, Workers Planned 2 | fixed ~64 kB tuple queues |
| `mv_soil_survey_grid` | `GroupAggregate` over `Gather Merge` **+ `Parallel Hash` Left Join** | **shared, RESIZABLE hash table** |

All three allocate a segment; one worker allocates one just as two do. What distinguishes the faulting
views is `Parallel Hash` — a shared hash table that **grows**, which is exactly what "could not
*resize* shared memory segment" reports.

**So the classification test for any view added to this lane is: does its plan contain `Parallel
Hash`?** Not "is it parallel?". If yes, set `max_parallel_workers_per_gather=0` on its spec. If no,
leave the default alone — the census plans have never faulted, and three of them sit at 96%, 94% and
71% of their own statement timeouts, so removing their workers would convert working refreshes into
permanent failures and feed the livelock stall 1 removes.

(`mv_soil_survey_union` also carries the `ST_CollectionExtract` omission at `drizzle/0029:918`.
Removing its memory fault will change *which* error it reports, not make it pass.)

### 3. A view can be too big for its own lane

The budget gate demanded `BUDGET_SAFETY_FACTOR × estimate` before starting a refresh. For
`mv_signal_cell_daily` that is `2 × 1,729 = 3,458 s` against a tick budget of 1,800 s —
**unsatisfiable on every tick**, so the view would have been reported `skipped_budget` forever. That
outcome is a healthy deferral everywhere else in this lane, which is what made the starvation
invisible.

`_required_budget_seconds` caps the requirement at the view's own `statement_timeout_seconds`, the
bound that is actually true: the statement cannot run longer than that. The doubling still governs
wherever it is the smaller number.

#### The tick budget is sized against the cron, not the spec table

`time_budget_seconds` must exceed every `statement_timeout_seconds`, or the view whose cap equals the
budget can only start on a tick where zero time has elapsed. But it must not simply be made large:

- The lane runs from an **hourly** cron. 2,100 s puts a worst-case tick (heaviest view at its 1,900 s
  cap plus ledger overhead, ~32 min) about 25 minutes clear of the next tick. An earlier draft used
  2,400 s and halved that slack.
- **This lane holds no `concurrency_key`** (verified NULL on the prod `job_definition` row). Two
  overlapping ticks each mint their own shard, and both would issue `REFRESH CONCURRENTLY` against the
  same views — they serialise on the matview lock rather than corrupting anything, but the second burns
  its lease waiting.
- `execution/jobs_pulse_command.py`'s own `DEFAULT_PULSE_TIME_BUDGET_SECONDS` is **600**, so a tick
  where this lane runs long lands every later pulse lane in `skipped_budget` — including the durable
  archive lanes, so the FIRMS backlog does not drain on such a tick. `skipped_budget` is not a dead
  letter, so the pulse still reports green. That is pre-existing (1,800 was already 3× 600) and is
  recorded here rather than fixed, because the fix is a scheduling decision above this lane.

### 4. The census re-grain, and what it actually buys

`drizzle/0031` adds `geo.mv_feature_observation_day_axis`; `drizzle/0032` repoints
`geo.v_observation_day_census` onto it behind a `relispopulated` precondition. Two files, because
**PostgreSQL refuses to read an unpopulated matview** — `materialized view "..." has not been
populated`, a thrown query, and no join type avoids it (a `FULL JOIN ... ON false` against an
unpopulated relation raises identically). Repointing the census before the populate would have 500'd
the entire layer-catalogue request, since `readObservationWindows` and the signal/drought axis both
select from that view.

**The re-grain is not a latency win, and an earlier claim that it was is retracted.** Measured against
prod: the axis populates in **286.8 s**, essentially the same as the combined statement's 283,049 ms.
Extrapolating a per-layer 0.82 s to "~22 s over 5.0M rows" was wrong by more than 13×, because the scan
and the `properties` detoast dominate at full scale and both variants pay them. What the split buys is
**spill width** — 33 bytes per tuple against 511, ~165 MB against ~1.4 GiB, the only axis a 3 GB cap
cares about — and **reliability**, since a 287 s statement completes under a 900 s cap and does not
under 300 s. Total refresh seconds per day go slightly *up*.

The wide relation drops to **six-hourly, not daily**, because two of its columns are not captions:
`metric_counts` feeds a `COALESCE(..., 0)` that turns a missing row into the false sentence
"`<label>` recorded no observation on `<date>`" (`environmental-read-model.ts:4204`, whose own comment
at `:4197` says that sentence "asserts something false about the warehouse"), and `newest_observed_at`
**selects `not_published` vs `stale`** at `:1452` — a status driver, not a caption. Six hours bounds
both windows; closing the false sentence properly is a TypeScript change tracked outside this lane.

A **ghost day** is the accepted residual of the FULL JOIN: a `(surface, day)` the wide relation still
holds after the axis has correctly dropped it lingers for up to one wide-relation cadence, so the
slider offers a day that draws nothing rather than hiding a day that has data. `drizzle/0032`'s header
states it.

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

## The SQL lives in sql/jobs/, not inline

Every non-trivial statement in `lease.py` and `worker.py` lives in
`src/agri_data_service/sql/jobs/<name>.sql`, loaded at module import time through
`agri_data_service.db.sql_queries.load_query_sql`. The constant name is the file name, lowercased
and stripped of its leading underscore, and each file opens with the same `-- <statement_name>`
marker comment the unit tests dispatch on. The one statement still inline is `_STATEMENT_TIMEOUT`:
`SET LOCAL` cannot take a bind parameter, so its seconds must be interpolated, and it is one line.

Three rules bind anyone editing those files:

1. **The marker stays line 1, byte-identical.** `tests/test_jobs_lease.py` and
   `tests/test_jobs_worker.py` match `^--\s+(\w+)\s*$` against the statement text and route stub
   rows and ordering assertions on the captured name. No other comment line in a file may be a bare
   `-- singleword`, or it becomes a second marker candidate.
2. **No comment may contain a colon immediately followed by a word character.** SQLAlchemy's
   `text()` scans the whole string for `:word`, comments included, and would mint a bind parameter
   nobody supplies. Write `work_item_id (uuid)` in a header, never the colon-led form.
3. **The comments are part of the statement text.** `str(text(load_query_sql(...)))` returns the
   walkthrough too, so prose alone can break an assertion that inspects SQL. Three spots are
   deliberately paraphrased rather than quoted for exactly this reason — the rejected aggregate in
   `advance_checkpoint_sequence.sql`, the fencing column name in `reclaim_expired_leases.sql`, and
   the availability fallback in `insert_job_work_items.sql`. Each carries an in-file note; do not
   paste the literal spelling back.

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

## The lane contract this ledger serves

This package is the durable runtime; `docs/layer-lane-standard.md` is the contract every lane built on it
must satisfy end to end (horizon, gap-to-work loop, governed absences, three crons, slider, agent tools).
Read it before registering a new lane -- the ledger is only the middle third of what a finished layer needs.
