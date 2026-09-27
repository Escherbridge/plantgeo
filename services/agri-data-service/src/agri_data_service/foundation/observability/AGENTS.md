# foundation/observability

## Responsibility

Wave O's logging contract: one `configure_logging`, secret redaction, the frozen vocabulary, event
names, and the one guarded arming call the package root makes at import time. Everything here is
`foundation`-admissible: no first-party import outside `agri_data_service.foundation` (see
`tests/test_layer_import_contract.py::LAYER_FORBIDDEN_IMPORTS["foundation"]` -- the forbidden set
also bans a bare `agri_data_service` import, not only `sqlalchemy`/`httpx`/`asyncpg`/`click`; a
self-import under `agri_data_service.foundation.*` is the one exemption the test carves out).

Design record: `.omc/research/ingestion-grill-20260926/observability-wave-design.md` (revision 2,
gitignored, main checkout only). Plan: `conductor/tracks/config_driven_ingestion_20260926/plan.md`
§0W.1 GL-1. Spec: §4.9.1.

## Logging contract

`logging.py::configure_logging(profile, *, long_running=False, console=False)` ports
`app.py::create_app`'s old inline `structlog.configure(...)` block and pins the processor order:

```
merge_contextvars -> add_log_level -> Railway level rename -> UTC TimeStamper -> turn context
  -> envelope (service, deploy) -> format_exc_info -> leaf normaliser -> redaction
  -> 16 KiB line clamp -> JSONRenderer(default=redacting_fallback)
```

`dict_tracebacks`, `ExceptionDictTransformer` and `show_locals=True` are never configured anywhere
in this service; `cache_logger_on_first_use=False` so a module-level `logger = get_logger(__name__)`
bound before `configure_logging` runs still picks up the real configuration once it does.

Two profiles: `service` (the executor and its armed children, the Sanic app, the runner from G1) --
debug/info/warn to stdout, error to stderr; `tool` (the CLI root for every group except `agent`,
manual `python -m agri_data_service.*` and `scripts/*.py` runs) -- every level to stderr, so stdout
stays the data channel. The `agent` group is exempt on purpose (another session's own stdout
discipline; `interface/cli/agent.py::reserved_stdout`) -- `root.py` skips `configure_logging`
entirely when `ctx.invoked_subcommand == "agent"`.

`logging.StreamSink` resolves `sys.stdout`/`sys.stderr` when it writes, never when configured, so a
context manager that swaps either stream mid-process (Click's `CliRunner.isolation`) is honoured
exactly while it is active and not after it exits.

Every pinned processor except the final renderer is wrapped in `_fail_open` (security review):
structlog does not catch a processor's own exception, so a fault anywhere in the chain (an
unexpected event-dict shape, an environment read gone wrong) now degrades to a stub line carrying
only `event`/`level`/`timestamp`/`service`/`logging_error`, rather than propagating into the
caller's `logger.info(...)` call site -- a log call must never be the reason a turn aborts. The
stdlib bridge (`_configure_stdlib_bridge`) now runs every THIRD-PARTY/stdlib record (Sanic's own
loggers, `py.warnings`, an unconfigured library) through a `structlog.stdlib.ProcessorFormatter`
built from the SAME pinned chain, instead of the bare `"%(name)s: %(message)s"` text it rendered
before -- those lines previously carried no envelope and no redaction at all. It also strips any
OTHER logger's own handlers and forces `propagate=True` on it (`_strip_foreign_logger_handlers`):
Sanic's `Sanic(...)` constructor attaches its own unredacted `StreamHandler`s to `sanic.root`/
`sanic.error`/`sanic.access`/`sanic.server` before `app.py::create_app` ever calls
`configure_logging`, so every Sanic error traceback (which can carry a keyed URL) was written raw to
stderr through Sanic's own handler, in addition to a second, redacted copy through this one.
`_RailwayLevelStreamHandler.handleError` is overridden for the same reason: the stdlib default
prints `record.msg`/`record.args` verbatim on a formatting fault, which bypasses every redaction path
here.

**Known gap, out of `foundation`'s file boundary:** `sqlalchemy.engine.Engine(echo=True)` sets its
own per-engine child logger's LEVEL directly (bypassing the `sqlalchemy` parent's WARNING floor this
module sets), and does so at engine-creation time, after `configure_logging` has already run --
`_strip_foreign_logger_handlers` cannot pre-empt a logger that does not exist yet. `db/engine.py`
(outside this module's owns list) is what decides whether `echo` is ever `True` in a real deployment
(`echo=settings.sanic_debug`); this module's own test coverage stays at the `debug`-level-never-
enables-it case it can actually pin (`test_engine_execute_under_debug_emits_no_sql`).

## Redaction

`redaction.py` is the one home for the pattern `jobs/lease.py::redact_text` and
`ingest/results.py::redact_secrets` used to each keep a private copy of (`redact_strict`, unchanged
byte for byte; both are now thin re-exports). `redact_for_log` adds an exact-value scrub (live
secret values collected from `os.environ`, `.env`, DSN passwords -- including realistic
`*_DATABASE_URL`/`*_URL` names outside the fixed exact-name set, when the value itself is DSN-shaped
and carries a password -- and `register_secret_values`, itself hardened to accept `str | bytes |
Iterable[str | bytes]` and fail closed on anything else), then an SQL-block cut (now also redacting
a driver's `query argument $N:`/`DETAIL: Key (...)=(...)` fragments), DSN-userinfo (greedy to the
LAST `@` before the path, not the first -- a password containing its own `@` previously leaked its
tail), secret query-parameter (matched with a look-behind so a `*_token`/`*_secret`-shaped name
after an underscore is caught, not only after a word boundary) and quoted-`'name': 'value'`, and
Bearer/Authorization (now consuming the credential AFTER the scheme word too, not only the scheme
itself) scrubs. `redact_value`/`redact_leaf` extend that to structured data and to the per-leaf
pipeline `logging.py` runs (scrub, then truncate to 8 KiB dropping the trailing partial token, then
regex -- in that order, so a secret that straddles the truncation point is already gone before the
cut can leave its prefix exposed); `redact_value` takes an optional `leaf_limit` so a caller that
wants the SAME 8 KiB per-leaf bound (`router.py`, over an already-parsed JSON object) can ask for it
without a second implementation. Key matching (`is_redacted_key`) is a predicate now, not only the
historical exact set (`open_meteo_api_key`, `cdsapi_key`, `receiver_writer_database_url`, `cookie`,
...), guarded so a URL/query-SHAPED key (one containing `/:?=&`) is never treated as a "key name" --
its own TEXT is redacted instead (a dict keyed by a URL that embeds `apikey=...` publishes that key
otherwise). `describe_error` gives only the class name for `sqlalchemy`/`asyncpg`/`psycopg`/
`psycopg2` exceptions (matched by module name, never by `isinstance` -- `foundation` may not import
any of them) and replaces the three `error=str(error)` sites in `routes/ops.py`. The exact-value
scrub's own cost is now memoized (`_dotenv_pairs` by the file's mtime, `_collected_secret_values` by
a cache key over the matching env/`.env`/registered values) rather than re-reading `.env` and
re-scanning `os.environ` on every single redacted string -- measured at 7 `.env` reads per log line
before this cache existed.

## Vocabulary is frozen

`vocabulary.py` is frozen at the end of GL-1: `ExitClass` (including `infra` and `report_missing` --
the latter fills BOTH this column and `TurnOutcome`, design §3.1's "exit 0, no report" row, the same
way `interrupted` already does for both; a code-review pass found it missing from `ExitClass` even
though `TurnOutcome` already had it), `TurnOutcome`, `IncidentKind` (now including the five GL-5/
Wave-O kinds design §3.3's table lists beyond o1's original set -- `lane_blocked`,
`lane_quarantined`, `executor_config`, `executor_repair_authoring`, `fleet` -- added even though o1
opens none of them, so o2b/GL-5 does not have to edit a vocabulary this file calls frozen), `MetricKey`,
`PoolLabel` and `LANE_LOGICAL_CAPS` (soil's 1,602 weighted hard cap, from `metadata.json`'s
`budget_headline.legacy_soil_per_run_after_g0`, pinned equal to G0's
`pipeline/direct/soil/source.py::open_meteo_request_weight` on soil's real request builder) are
append-only from GL-2 onward -- nothing here is renamed or removed without a fresh track. Every
`_LITERAL_NAME_SET` below its `Literal` is now derived with `typing.get_args`, not hand-duplicated --
a hand-duplicated set is exactly how `report_missing` above drifted between the two columns; the
`Literal` itself is still spelled out by hand (that is what a type checker reads), but the RUNTIME
membership set can no longer silently fall behind it. Two names are deliberately absent:
`POOL_BULK_LANES` and `pool_saturated` (WQ-4 declined the GL-5 pool brake for this wave), alongside
`budget_deferred`/`budget_basis_suspect` (G1's admission refusal, also out of Wave O's scope).
`events.py` holds the `plantgeo_<component>_<noun>_<verb>` event-name constants the later slices
(o2-o5) share; GL-6's hold-probe/probation events and G1's `budget_deferred` are out of Wave O's
scope (folded into `f1-executor`) and are appended there, not here.

## Arming

`bootstrap.py::arm_from_environment()` is called exactly once, guarded, from
`src/agri_data_service/__init__.py`. Every branch fails open:

1. **Executor child** (`PLANTGEO_TURN_ID` set): configures `service`, raw-writes one
   `plantgeo_turn_usage_open{pid}` line to stdout, and registers an `atexit` usage writer. **No
   signal handler is installed** -- SIGTERM keeps Python's default disposition (SOF2-06); a handler
   that tried to "clean up" would turn Railway's ordinary redeploy signal into a hang.
2. **No turn id, but `sys.orig_argv` names a `-m agri_data_service.*` run or a `.py` under this
   service's `scripts/`**: configures `tool`. Matched on the `-m` MODULE NAME or the script's own
   FILENAME STEM against `pytest`/`_pytest`/`sanic`/`alembic` -- never on whether the whole joined
   argv happens to contain one of those words as a substring, which previously false-excluded any
   invocation whose ARGUMENTS merely contained one of them (`scripts/sanic_helper.py`, say), and
   missed the common relative form `python scripts/foo.py` run from the service root (the old
   `/scripts/` substring check needs a leading `/`, which that path never has) -- both corrected by
   security review.
3. **Otherwise**: registers an `atexit` operator summary, skipped under pytest, when
   `configure_logging(long_running=True)` already ran (now actually recorded --
   `logging.py::is_long_running_configured()` -- where it used to be `del`eted and silently
   discarded, so this skip could never fire), or when nothing has been metered this process
   (`_no_hosts_metered`, checked at the DECISION point rather than relied on `usage.py`'s writer to
   skip on its own, which it never did -- see "Usage line" below; security review).

### The bootstrap <-> usage.py contract

`usage.py` (host/provider/pool resolution, the Open-Meteo weight table, the usage line's actual
content) is GL-1b's file, authored immediately after this one against the same `vocabulary.py`.
`bootstrap.py` cannot import it directly at module load time -- it may not exist yet when this
commit lands -- so it imports it **lazily, by name**:
`agri_data_service.foundation.observability.usage.write_usage_line(pid=..., opening=...)`. An absent
module or function is a silent no-op. Keep this name in sync in both files if it ever changes; there
is no other coupling between them.

## Usage line (`usage.py`, GL-1b)

`provider_for_host(host)` resolves a bare host to a `HostResolution(provider, pool)` pair (design
§2.3): `customer-*.open-meteo.com` -> `open-meteo`/`open-meteo-paid`; any other `*.open-meteo.com`
-> `open-meteo`/`open-meteo-free`; `firms.modaps.eosdis.nasa.gov` -> `firms`/`firms`;
`waterservices.usgs.gov` -> `usgs-water-data`/`usgs-water-data`; anything else is `None`. Order
matters -- the `customer-` rule is checked first, or a paid host would also match the free rule.

`open_meteo_request_weight(locations, days, variables)` is a **byte-identical duplicate** of
`pipeline/direct/soil/source.py::open_meteo_request_weight`, pinned equal by
`test_weight_equals_g0_on_soil_request_builder` -- not a shared import, since `foundation` may not
import outside itself. **No `models` factor**, even though the design record's general weight-rule
prose (§2.1) writes `weight = locations x max(1, days/14) x max(1, variables/10) x models`: G0's real
soil request builder, the one this table is pinned against, never applies one (confirmed by reading
`source.py` directly at HEAD `174e60e2` -- its signature takes exactly three arguments). That prose
line is generic language for a different lane's URL shape, not this one; a lane that actually needs
a `models` multiplier extends `open_meteo_weight_for_url`'s table, it does not change this pinned
function. `open_meteo_weight_for_url(url)` parses the five URL shapes design §2.1 lists (dated span;
`past_days`/`forecast_days` with `forecast_days` defaulting to 7 on `/v1/forecast`, 0 elsewhere;
`current=` alone -> 1 day; neither present -> the endpoint default; `variables` summed across
`hourly`/`daily`/`current`/`minutely_15`) and calls the pinned function above.

`write_usage_line(*, pid, opening)` is the exact function `bootstrap.py` imports lazily by name.
With a turn id in the environment (`PLANTGEO_TURN_ID`) it raw-writes to stdout: `opening=True` the
`plantgeo_turn_usage_open` marker, `opening=False` the flat `plantgeo_turn_usage` line (`hosts` <= 16
entries, the rest folded into `other`; `rss_peak_kib`, `cpu_seconds`, `meter_errors`,
`last_send_outcome`, `last_send_at`; `level: debug`, consumed internally by the router, GL-3). With
no turn id, `opening=False` raw-writes the `plantgeo_source_usage` audit line to stderr instead
(`run_origin: operator`, `entry` from `sys.orig_argv`, redacted), at `warn` when `hosts` touched a
weighted pool (`open-meteo-paid`/`open-meteo-free`; soil is the only weighted Wave-O lane today) and
`info` otherwise -- **called directly, `write_usage_line` always writes, even with zero hosts**,
since "no sends this process" is itself the audit answer. The exit-line SKIP for a zero-host,
non-long-running, unarmed process lives in `bootstrap.py::_register_operator_summary_at_exit`'s
decision, not here (security review corrected an earlier version of this paragraph, and of that
function's docstring, which claimed the opposite -- that this writer itself skipped a zero-host
operator line; it never did, and `test_zero_host_usage_line_is_still_written` in `test_usage.py`
still pins that direct-call behaviour). `_host_counters` is the module-level accumulator
`ingest/http.py`'s metering hooks populate once `o3-ingest-meter` (GL-2) lands; it is empty at GL-1,
by construction, so every usage line written before GL-2 legitimately reports zero hosts.
`_rss_peak_kib()` returns `None` wherever `resource` does not exist (Windows; import-guarded at
module load, matching design §6.2's Portability rule) or lacks the attributes mypy expects on a
platform it does not resolve for (reached through `getattr`, not a direct attribute access);
`cpu_seconds` instead always comes from `time.process_time()`, which has no such gap. `write_usage_line`
now redacts its own payload (`redaction.redact_value`) before serialising -- GL-2 will fill
`last_send_outcome` from real request/response data, and neither this writer nor
`router.py::_consume_usage_line` redacted it before (security review).

**A mismatch found in GL-1a's `bootstrap.py`, not fixed here (out of this seam's file list):** its
own module docstring and the "bootstrap <-> usage.py contract" section above describe
`write_usage_line(pid=..., opening=...)` as the one contract point, but neither of `bootstrap.py`'s
two real call sites (`_arm_executor_child`'s `atexit` hook, `_register_operator_summary_at_exit`'s)
ever passes `opening=True` -- `_arm_executor_child` writes its own `plantgeo_turn_usage_open` line
inline via `_raw_write_stdout`, bypassing `usage.py` entirely for that half. `write_usage_line`
here still honours `opening=True` faithfully (and produces the identical line bootstrap's own inline
version does), so nothing is broken -- the branch is simply unreachable through `bootstrap.py` as it
stands today. Left for whoever revisits `bootstrap.py` to decide whether it should route through
`usage.py` instead of writing that line itself.

## Child log router (`router.py`, GL-1b; module only -- not wired until GL-3)

`ChildLogRouter(attempt_id=..., clock=...)` reassembles a child's raw stdout/stderr into lines
(`feed(stream, chunk)`; a 64 KiB reassembly cap flushes an unterminated line as a
`plantgeo_child_output` stub), routes and levels each one (`route_line(stream, raw)` is the direct,
already-delimited-line test seam `feed` also uses internally), and tracks the turn-usage pid pairing
(`usage_summary()`).

Bounds: a JSON line over 64 KiB, or a still-unterminated buffered line over the 64 KiB reassembly
cap, becomes a `plantgeo_child_output` stub (`bytes`, `stream`, the redacted-then-truncated first
1 KiB preview -- exact-value scrub always runs before the slice, never after, so a secret straddling
the cut cannot leak its prefix). A parsed JSON object gets its missing turn-context fields filled
from the same six `PLANTGEO_TURN_*`/`PLANTGEO_LANE_ID`/`PLANTGEO_ATTEMPT` environment variables
`logging.py` reads (duplicated locally as `_TURN_ENV_TO_FIELD` -- kept in sync by hand with
`logging.py::_TURN_ENV_TO_FIELD`; these are peer sibling modules so a same-package import would pass
the layer-import test, but reaching into a sibling's private constant is a worse coupling than five
duplicate lines), and a missing `level` from `LEGACY_LEVEL_OVERRIDES` (sourced from design §1.4's
"Existing executor events" list) or else the stream's default (`info` for stdout, `error` for
stderr -- Railway's own unlevelled-line rule, applied here rather than left to Railway's fuzzy
match). A non-JSON line is scrubbed, then truncated to 16 KiB (same drop-the-trailing-token rule as
the 1 KiB preview), then regex-scrubbed; a third-party warning prefix (`^[\w.]+Warning: `,
`^Warning \d+: `) routes as `warn` regardless of stream. A 50 lines/s sustained, burst-200 token
bucket (`_RateBucket`, per attempt) drops everything else once exhausted, except `level == "error"`
and the two events `plantgeo_lane_turn_report` / `plantgeo_job_executor_lane_turn`, which are never
dropped; the first drop in an attempt's lifetime emits exactly one `plantgeo_child_log_truncated`
carrying the running `log_lines_dropped` count. Independent per-attempt CEILINGS also apply
(security review; design §1.8): 1,000 non-error / 200 error / 500 debug lines, and 1 MiB total --
`error` is exempt from the rate bucket but still capped here, since an unlevelled stderr line
defaults to `error` and previously bypassed volume control entirely. `plantgeo_turn_usage_open`/
`plantgeo_turn_usage` lines are consumed rather than forwarded: `usage_summary()` reports
`usage_complete=False` for any pid whose open was never matched by a close, and the winning
`last_send_outcome` is whichever usage line carries the latest `last_send_at` (redacted before
comparison, since it is free text that can carry a keyed URL). `PLANTGEO_LOG_ROUTING=off` disables
the LEGACY-TABLE re-leveling and turn-context rewriting only; a level is still always stamped
(`_default_level_for_stream`) even with routing off, so every routed line still satisfies FR-30's
"every line has event/level/timestamp/service" -- **reconciling design §1.6.7's "re-leveling and
rewriting" prose against spec §4.9.1's "disables re-levelling only" wording**: the DEFAULT-level
stamp is not a re-level (nothing already-present is overwritten), only the legacy-table override and
turn-context fill are what routing controls. Redaction is never disabled by any switch. `event`/
`timestamp`/`service`/`deploy` (`null` when `RAILWAY_DEPLOYMENT_ID` is unset) are now stamped on
EVERY routed line, JSON or not, on either stream -- a forwarded non-JSON stderr line previously
carried no `event` at all (security review). The `[SQL: ...]` cut is now STATEFUL per stream
(`_apply_sql_block_state`): `feed()` splits a child's raw output into lines before any redaction
runs, so a driver's `[parameters: {...}]` line -- one line further down from `[SQL: ` in a real
traceback -- previously reached `redact_for_log` unmarked; every line of the same stream after one
containing `[SQL: ` is now replaced outright until a blank line or a new record/traceback starts,
and a bare `[parameters:` line is always replaced regardless of block state. A fault in the
structured path falls back to line-wise `redact_for_log` over the already-bounded text; a fault in
that fallback too drops the line and counts it -- redaction itself is never skipped to avoid a
crash.

**Test-name gap, documented rather than silently patched over:** plan 0W.1 and this design record's
own §6.1 both describe `test_router.py` as "revision 1's eleven tests plus" five newly named ones,
but neither document, nor `observability-wave-design.result.json`, nor anything else in this
research directory preserves what those eleven were actually called -- only revision 2 of the design
record survives. The eleven in `test_router.py` are therefore freshly authored against this section's
prose rather than recovered verbatim; each one is named for the behaviour it exercises.

## Railway logging facts (A23; read before GL-1, per plan 0W.1)

Full research trail: `.omc/research/wave-o-20260927/railway-logging-docs.md`.

- **Log-level mapping.** Railway recognises `debug`, `info`, `warn`, `error` from a JSON line's
  `level` field, case-insensitive and fuzzy-matched to the nearest of those four (so `warning`
  normalises to `warn`). Unlevelled `stdout` defaults to `level.info`; unlevelled `stderr` is forced
  to `level.error`. This is why `critical`/`exception`/`fatal` are renamed to `error` here rather
  than left for Railway's fuzzy match to land on: the mapping is real, but this service pins its own
  answer instead of depending on it.
- **Per-replica log-rate limit.** 500 log lines per second per replica, flat across every plan and
  not tunable (a third-party forwarder cannot raise it either). Lines over the limit are **silently
  dropped**, with one `Railway rate limit ... reached ... Messages dropped: N` warning line, not
  queued or delayed. This is the volume ceiling the router (GL-1b/GL-3) budgets against; GL-1 itself
  emits nowhere near this rate.
- **Maximum log line size.** Not documented anywhere in Railway's own docs (`observability/logs`,
  the debug-production-incident, ship-logs-third-party and structured-logging-production guides,
  and `networking/public-networking/specs-and-limits` were all checked, plus five targeted searches
  for "max log line size" / "truncation" / "characters/bytes limits"). This service's own 16 KiB
  own-line / 64 KiB child-JSON bounds (design §1.8) are therefore a self-imposed ceiling, not one
  Railway's platform is known to enforce or reject past.
