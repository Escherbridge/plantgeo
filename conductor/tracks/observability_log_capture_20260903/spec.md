---
type: track-spec
slug: observability_log_capture_20260903
title: Observability lane — durable error/warning capture and the operator panel
status: chartered-not-started
owner_decisions_required: 6
blocked_by: parquet cutover wave in flight (QUALITY_RECEIPT tree digest)
created: 2026-09-03
---

# Purpose

Make "why is this lane red at 02:13?" answerable after Railway's log scrollback is gone, without a
whole-stream LIST on the warehouse bucket and without publishing a DSN into an object that lives
forever.

Three deliverables, in dependency order: (1) a real structured-log configuration for the executor
process, which does not exist today; (2) a durable, DuckDB-queryable sink in a **separate** bucket;
(3) a panel region that reads it.

## D1 RESOLVED 2026-09-04 by the orchestrator — `/ops` is NOT publicly reachable

The charter's largest unknown and its top risk. Checked with the Railway API: `plantgeo-parquet-api`
(`33aed861-af76-4fdd-a95e-784bdcc95e55`) and `plantgeo-job-executor`
(`565ecaad-9946-48f1-8a0b-28fa60494a16`) both return `customDomains: []` and `serviceDomains: []` in
the production environment. Neither has a public ingress; `/ops` is reachable only over the Railway
private network.

**Therefore W6 (the bearer gate) is a prerequisite for W7, not an incident response.** The exposure
is real but latent: the day anyone generates a domain on either service, an unauthenticated operator
console becomes public. Build the gate before the region, and treat "generate a domain on the agri
service" as an action that requires the gate to exist first.

# Discovery answers

## 1. Sanic — YES, it is the whole HTTP surface
`src/agri_data_service/app.py:34` — `app: AgriApp = Sanic("agri-data-service")`, served by
`Dockerfile:87` (`exec sanic agri_data_service.app:create_app --factory`). Blueprints mount per
`SERVICE_PROFILE` at `app.py:103-115`; `plantgeo-parquet-api` is this same image on the
`published_reader` profile.

**Finding:** `app.py:118-121` mounts `health_bp`, `ops_bp` and `agent_bp` **unconditionally, outside
the profile map**, so `/ops` is on `published_reader` too. `ops.py:1056` renders its own warning:
*"/ops is not authenticated. Do not expose this service publicly until it is."* See D1 above.

## 2. Datastar — YES, vendored and already streaming
`static/datastar.js` (30,732 bytes, added 2026-08-08), served from disk at `ops.py:407-416`. SSE loop
at `ops.py:376-404`; Datastar v1 `datastar-patch-elements` frame encoder at `ops.py:1024-1032`.
Extension point at `ops.py:1009-1021`: `_regions()` returns nine `(css_selector, html)` pairs, so
**adding a panel is appending one tuple and writing one `_render_*` function.** Adoption cost: zero.

## 3. Two panels exist, and they are not duplicates

| | Next.js `/admin/jobs` | Sanic `/ops/backfill` |
|---|---|---|
| Entry | `src/app/admin/jobs/page.tsx` | `ops.py:354` |
| Reads | tRPC → `agri.job_definition`, `agri.job_run` | 6+ raw SQL files at three grains |
| Auth | **role-gated** (`src/app/admin/layout.tsx`) | **none** |
| Reach | the app's `DATABASE_URL` | `receiver_writer_session`, the warehouse role |

`/ops` has decisively better data access and is the only one answering rate, ETA, stall and
ledger-vs-landed. `/admin/jobs` has the auth gate. **Extend `/ops`; add the gate it lacks rather than
moving the work to the surface that has one.**

## 4. Structured logging — LESS than assumed. The load-bearing finding.
`structlog.configure(...)` appears at **exactly one** call site: `app.py:45-56`, inside
`create_app()`. The executor does not call it — `railway.job-executor.json` starts
`agri-service ops jobs-executor`, which resolves through `pyproject.toml:53` to
`interface/cli/root.py:11-19`, a bare `click.group()` that configures nothing.

**So the executor's structlog output is structlog's DEFAULT `ConsoleRenderer` key=value text, not
JSON.** A Railway log tap built on the JSON assumption would be regex-parsing colorized console
text — the exact fragility the design set out to avoid.

Two more facts from the same file:
- `job_executor_service.py:1750` — `click.echo(json.dumps(summary.to_dict(), sort_keys=True))`. The
  tick summary IS real JSON, on **the same stdout** as the console-rendered structlog lines. The
  stream-mixing hazard is already present.
- `job_executor_service.py:1659` — `asyncio.create_subprocess_exec(*spec.command)` with **no
  `stdout=`/`stderr=`**. Every lane subprocess inherits the executor's file descriptors; its output
  goes straight to Railway, interleaved, and **the executor never sees a byte of it.** This one line
  decides the architecture.

Event vocabulary is stable and filterable without regex: `ExecutorTickSummary.to_dict()`
(`:926-933`) and `LaneTickResult.to_dict()` (`:901-913`). Levels: `logger.error` for
`plantgeo_job_executor_tick_unhealthy` (`:1752`) and `..._tick_failed` (`:1770`), `logger.info` for
`..._healthy` (`:1757`).

## 5. Receipt-writing discipline already exists — reuse it
`pipeline/parquet/objectstore.py`: `ObjectStore` facade (`:481`), `from_settings()` (`:490-495`),
`BotoObjectStoreBackend` (`:428-478`) behind a `Protocol` (`:225-236`) so tests never touch a
network. JSON receipts already written as objects: `write_completion_marker` (`:622`),
`write_absence` (`:585`), `write_availability_retry` (`:687`, 8 MiB ceiling at `:119` with an
explicit over-size refusal at `:689-692`). `WrittenObjectLedger` (`:369-406`) is the accumulation
pattern to copy.

**Redaction exists and is proven:** `ingest/results.py:34` `_SECRET_SHAPED`, `redact_secrets()` at
`:71-73`, `failure_reason()` at `:76-83` which redacts BEFORE clamping (deliberately, `:81`) and
refuses to echo SQLAlchemy messages at all (`:78-80`) because they carry bound parameters. A
deliberate twin lives at `jobs/lease.py:142-144`; they must change together.

# Recommended architecture — three layers, not an either/or

`job_executor_service.py:1659` makes "tick receipt vs Railway tap" a false choice: they see different
things and neither is a superset.

- **Layer A — executor tick receipt (W3).** `put()` the `summary.to_dict()` that already exists. No
  buffering, no parser, no Railway token, no 403 hazard. Answers "what did the executor decide, which
  lanes were due, which failed."
- **Layer B — in-process child capture (W4).** Change `:1659` to `stdout=PIPE, stderr=STDOUT`, drain
  with a hard byte cap, keep a bounded tail (64 KiB proposed), redact, attach to the failing
  `LaneTickResult`. Beats the Railway tap for the same bytes on five axes: no credential, no
  403-on-User-Agent trap, no rate limit, no retention window, and above all **causally attached to a
  `run_id`** — the API returns a time range and leaves you guessing which lines belonged to which run
  of which concurrently-running lane. Also fixes the interleaving at `:1659` as a side effect.
- **Layer C — Railway API tap (W5), the backstop.** Scoped to exactly what A and B cannot see:
  deployment-level events and **death before the tick lands** (OOM kill, container restart, platform
  eviction, a crash inside `run_executor_tick` before `:1750`). Slow clock (15 min, trailing 20 min
  window with overlap), `error`/`warning` severity only, redact, one object per window.

The owner asked for the API tap specifically and gets it, doing the job only it can do. What is
refused is making it the *primary* path for lane diagnostics — routing bytes the executor already
holds out to a third-party API and back is more machinery, more secrets, and less correlation.

**Rejected outright: a general log firehose.** Object stores cannot append, so it needs a buffer, and
a buffer loses its contents on SIGKILL — precisely the failure you most want logged. At
`ConsoleRenderer` output it is regex over colorized text. Volume is unbounded and unknown. And it
produces objects nobody has a query for.

**W1 is a prerequisite, not a layer.** Configure structlog in the CLI so every downstream design
rests on JSON rather than console text — and split the streams:
`logger_factory=structlog.PrintLoggerFactory(file=sys.stderr)`, leaving `click.echo` (stdout) the
sole owner of the machine-readable tick line.

# Bucket

**Name proposal:** `plantgeo-observability`.

> **Trap, from project memory.** A Railway bucket's *display name* is not its S3 bucket name (the
> Parquet bucket displays as one thing and is `plantgeo-parquet-9ymvp7gv`). Read the actual bucket
> name off the service and put THAT in `OBSERVABILITY_STORE_BUCKET`. Region is `auto`, not a
> geographic code (`config.py:166`).

New settings mirror `config.py:163-172` exactly: `OBSERVABILITY_STORE_ENDPOINT_URL`, `_REGION`
(default `auto`), `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`. Reuse `ObjectStoreCredentials`
(`config.py:24-30`) and `BotoObjectStoreBackend` (`objectstore.py:410`) unchanged. **No prefix mode
— one bucket, one purpose.**

## Object layout

```
kind=executor-tick/year=2026/month=09/day=03/tick-<observed_at>.json
kind=lane-detail/lane=<slug>/year=2026/month=09/day=03/run-<run_id>-seq-<0000>.json
kind=platform-window/service=<slug>/year=2026/month=09/day=03/window-<start>-seq-<0000>.json
```

Three load-bearing properties:
1. **The top segment is `kind=`, never `layer=`.** Every warehouse path parser anchors on
   `layer=<slug>/kind=<kind>/zoom=z<NN>/…`, so a `kind=`-rooted key cannot match any of them even if
   it somehow landed in the data bucket. Belt and braces behind the separate-bucket decision.
2. **The key IS the idempotency key** — `observed_at`, `(run_id, seq)`, `(window_start, seq)`.
   `put()` is an unconditional overwrite, so a retry overwrites itself and cannot duplicate.
3. **Day-partitioned**, so a query names its days and reads a month without listing the bucket.

## What is lost on SIGKILL, plainly
- **Layer A:** the in-flight tick (the write happens after `run_executor_tick` returns). Bounded — the
  next tick re-reports the same lane conditions.
- **Layer B:** the tail of a lane killed mid-run. The `agri.job_attempt` ledger row still exists, so
  the bucket loses the text, not the fact.
- **Layer C:** up to one poll interval. Deliberately the layer that observes kills, and deliberately
  the one with the smallest buffer.

**Nothing is buffered across ticks.** Every object is written whole, at a natural boundary, in one
`put()` — the entire reason the tick-receipt shape was chosen over a line stream.

# Panel: extend `/ops` (Sanic + Datastar). Not Next.js.

One tuple in `_regions()` plus one `_render_*` function, against an already-vendored Datastar with no
build step. The Next.js panel would need a new tRPC router, a new client component, and decisively
**has no credentials for the observability bucket and no reason to acquire them** — it reads the app
database, not object storage.

**The gate blocks the region.** Adding durable, redacted-but-real error text to an unauthenticated
page is a strictly larger exposure than today's lane names and shard keys. Recommended gate: the
receiver's bearer-token discipline already proven in `routes/local_publication.py` (see
`routes/AGENTS.md:3` — fail-closed unless enable switch, strong bearer token and audit actor are all
present), applied as blueprint middleware on `ops_bp`.

# Wave plan

**Gate: nothing starts until the parquet cutover's wave lands and `QUALITY_RECEIPT.json` is green.**
`scripts/quality_receipt.py:38-39` digests `("src", "tests", "scripts", "alembic", "db")` — every file
below is inside that digest.

| Wave | Owns (exclusive) | Depends on | Deliverable |
|---|---|---|---|
| **W1** | `interface/cli/root.py`, `observability/logging.py` (new), `tests/observability/test_logging_config.py` | — | Shared `configure_structured_logging()`; JSON renderer to **stderr**; `app.py` refactored to call it |
| **W2** | `observability/store.py`, `observability/paths.py` (new), `config.py` (append-only), their tests | W1 | Second-bucket credentials + `ObservabilityStore`; key builders with round-trip tests. No writer wiring |
| **W3** | `execution/job_executor_service.py`, `tests/execution/test_job_executor_receipt.py` | W2 | Tick receipt after `:1750`; a write failure is logged and never fails the tick |
| **W4** | `execution/job_executor_service.py` (**serialize after W3 — same file**), `tests/execution/test_lane_capture.py` | W3 | `:1659` gains `stdout=PIPE, stderr=STDOUT`; bounded tail; redacted; attached to `LaneTickResult` |
| **W5** | `observability/railway_client.py` (new), `interface/cli/ops.py` (+one command), its tests | W2 | Error/warning-only platform-window tap. **Explicit `User-Agent`, asserted by a test** |
| **W6** | `routes/ops_auth.py` (new), `app.py`, `tests/test_ops_auth.py` | — (parallel with W1–W5) | Bearer gate on `ops_bp`, fail-closed |
| **W7** | `routes/ops.py`, `tests/test_ops_routes.py` | W3, W6 | One `#ops-observability` region + `_render_observability()` |

**Collision note:** W3 and W4 both own `job_executor_service.py` and must not run in parallel.
Everything else is disjoint. Per the standing rule, no wave runs tests; one sweep at the end by a
separate reviewing agent.

# Tripwires

1. **Coverage.** Assert `try_parse_partition_path`, `try_parse_absence_marker_path` and
   `try_parse_completion_marker_path` all return `None` for every key shape `observability/paths.py`
   can produce; and assert `ObservabilityStore` and `ObjectStore` resolve to different buckets,
   refusing construction when equal. The widest warehouse listing (`drain.py:1134`) is scoped to
   `layer=<slug>/`; the `kind=` root is outside it by construction and the test keeps it true.
2. **User-Agent.** A test asserting the Railway client sends an explicit, non-default `User-Agent`.
   Header precedent: `ingest/mtbs.py:189-194`, `ingest/sensors.py:140-147`.
3. **Redaction.** Feed a DSN, a keyed path and a bearer token through the write path and assert
   `[redacted]`. Reuse `redact_secrets` — do not write a third regex.
4. **Stream separation.** Assert the executor's stdout, captured alone, parses as a stream of JSON
   objects with nothing else in it.
5. **stdout survives.** Assert the tick still `click.echo`s when the bucket write raises. The sink
   being broken must not take the executor with it.
6. **Volume.** A module constant capping bytes per lane-detail object, with a test feeding oversized
   child output and checking the tail is clamped. Mirror `MAX_AVAILABILITY_RETRY_BYTES`
   (`objectstore.py:119`).

# What this does NOT do

- Replace Railway's log view. stdout stays; this is a durable copy of a filtered subset.
- Capture info/debug. Error and warning only: Layer A writes only when `summary.failed` or on a slow
  keepalive; Layer B only on non-zero exit, timeout or fence loss; Layer C on Railway's severity field.
- Log search, alerting, paging, or a retention policy (see D6).
- Touch the Next.js admin panel — `src/app/admin/**` and `JobRunnerDashboard.tsx` are out of scope.
- Instrument the Sanic web process. Executor and platform only.
- Fix the ledger abandonment in `routes/AGENTS.md:23`.
- Run PlantGeo locally, migrate, or deploy.

# Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| ~~D1~~ | ~~Is `/ops` already public?~~ | **RESOLVED 2026-09-04: no domains on either service. Gate before the region; treat generating a domain as gated on W6.** |
| **D2** | Railway bucket or Cloudflare R2? | Railway bucket — same provider as the executor, no cross-cloud egress |
| **D3** | Auth mechanism for `/ops` | Bearer token, fail-closed, mirroring `local_publication.py` |
| **D4** | Lane-detail tail cap | 64 KiB |
| **D5** | Healthy-tick keepalive, or errors only? | One healthy tick per hour — errors-only makes a stopped executor indistinguishable from an idle one, the exact ambiguity `routes/AGENTS.md:19` records as already biting the walks panel |
| **D6** | Retention | 90 days, as a bucket lifecycle rule, not in code |
| **D7** | Tap all `plantgeo-*` services or executor only? | Executor only in W5; widen once proven |

# Risks

1. **Layer B changes the executor's process model, and everything depends on the executor.** `:1659`
   currently inherits fds — cheap, zero-copy, unbounded. `PIPE` puts every child's output through the
   parent's event loop; a chatty lane that costs nothing today could stall the heartbeat (`:1604`) and
   cost itself its fence. Mitigation: bounded drain with an explicit cap and a test; W4 is last among
   the executor waves so it can be reverted alone.
2. **Redaction is a regex, and regexes miss.** `_SECRET_SHAPED` catches scheme-shaped, `user@host`-
   shaped and query-tail-shaped tokens. It does not catch a bare 40-character API key sitting in a log
   line with no URL around it. In scrollback that survives a window; in a bucket it survives forever.
   Mitigation: cap what is captured, separate bucket with its own credentials, retention (D6), and
   never describe this as "redacted, therefore safe to make public."
3. **The exposure is latent, not absent.** D1 is resolved today, but it is one dashboard click from
   changing.

# What could not be determined

- **The Railway log API's actual shape** — endpoint, severity field name, retention, rate limits,
  line-length truncation, and whether it separates stdout from stderr. If Railway truncates long
  lines, the tap cannot reconstruct a traceback, which strengthens Layer B further. **W5 must begin
  with a documentation read, not a code write.**
- **Whether the 403-on-Python-User-Agent note applies to the log/REST API or only the GraphQL write
  path** it was recorded against. Set the header regardless — one line and a test.
- **Actual tick volume.** `DEFAULT_POLL_SECONDS` was not read, so Layer A's object count and cost are
  un-sized. Read it before D6.
- **Whether an observability bucket already exists.**
