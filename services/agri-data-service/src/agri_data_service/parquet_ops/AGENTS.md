---
type: module-notes
---

# `parquet_ops/` — one reusable Parquet operations core

Protocol-independent parsing, four-state resolution, coverage, wire rendering, object reads, typed
refusals, and bounded DuckDB execution. HTTP, CLI, and agent adapters consume this package; this
package must never import a surface package.

## The contract is frozen elsewhere, and this directory obeys it

`tests/contract/wire_contract.py` is the declaration; `tests/contract/fixtures/*.json` are the nine
golden payloads; `src/lib/server/services/parquet-plane-client.ts`'s `WIRE` block is the TypeScript
half. **Nothing here may add a field.** `wire.py` spells every route segment and parameter name once
on the serving side and `tests/parquet_ops/test_wire_agreement.py` compares that spelling against the
frozen table, so a rename on either side fails the build.

Five behaviours the contract encodes, and where each one lives:

1. **All four states carry a `state` field** — the HTTP adapter renders resolved envelopes as 200;
   `ServingRefusalError` is a statement about serving and never about content.
2. **A window answers every day in its closed range, ascending** — `serving.resolve_window` walks the
   span itself and emits one envelope per day; there is no path by which a day is skipped.
3. **A carried-forward release is reported at its own day** — `serving.resolve_release` returns
   `served_day = max(resolvable days <= as_of)` and carries the requested day beside it.
4. **Days are ISO string prefixes** — `wire.render_day` is `date.isoformat()` and
   `request_params.parse_calendar_day` refuses anything carrying a `T` or a `Z` rather than
   truncating it. Nothing in this directory converts a zone near a `*_day` value.
5. **Coverage is per lane and tier-agnostic** — `coverage.build_lane_coverage` unions the four
   published tiers before it decides anything, and a lane with no written data reports `null` bounds.

## Why the memory ceiling is not advisory

`duckdb_session.py` sets `memory_limit`, a `threads` cap, `max_temp_directory_size='0GiB'` and
`TimeZone='UTC'`. Spilling is DISABLED. That is a guard, not tuning: on 2026-08-24 an unbounded local
DuckDB query cross-joined ~140,000-vertex USDM polygons per output row and spilled until it consumed
the host (`analysis/AGENTS.md`). With spilling off the same query raises in about a second. **These
routes run DuckDB on request paths, so every session opened here carries the block, and the session
is opened per read rather than pooled** — a shared connection would let one oversized read spend the
budget the next one relies on.

**The per-session limit is only half the guard.** `duckdb.connect()` with no argument creates a NEW
database instance, so `memory_limit` binds to one connection and not to the process. With
`asyncio.to_thread` (default executor, `min(32, cpu + 4)` threads) and a session per request, the
process ceiling was 16–32 × `SERVING_MEMORY_LIMIT` — the 2026-08-24 incident reachable again through
concurrency instead of through one query. The other half is `duckdb_session._read_pool`, a
`ThreadPoolExecutor` of exactly `SERVING_MAX_CONCURRENT_READS` threads: at most that many `work()`
calls run, so at most that many sessions exist, and the ceiling is that number × the per-read limit.
`test_the_process_memory_ceiling_bounds_concurrency_times_the_per_read_limit` asserts the product
against `SERVING_PROCESS_MEMORY_CEILING_BYTES`, so raising either number alone fails the suite.
In front of the pool is a per-loop `asyncio.Semaphore` whose job is not the ceiling but the **clean
fault**: a read that cannot get a slot inside `SERVING_SLOT_WAIT_SECONDS` raises the typed
`serving_at_capacity` refusal rather than queueing. `run_serving_read` takes admission before it
opens a session. The slot is released by the underlying worker's completion callback, not by caller
cancellation, so a timed-out request cannot admit a fourth session while its abandoned query runs.

**Clip before probing** is the companion discipline and lives in `warehouse_reader._clipped_scan`: a
geometry lane read with a bbox is served as `ST_AsGeoJSON(ST_Intersection(geom, envelope))`. Measured
here 2026-08-25 against the 2026-08-04 USDM release: the largest polygon falls from 124,676 vertices
to 6,151, with no precision loss inside the region actually requested. `ST_Simplify` was evaluated
upstream and rejected as the primary lever — it can move a boundary and flip a cell.

**A clip may not change a row's geometry TYPE.** `ST_Intersects` is true for boundary contact, so a
polygon touching the viewport edge clips to a `LINESTRING` and one touching a corner clips to a
`POINT` — served under a schema promising a Polygon, and undrawable by a fill renderer. The clip is
therefore wrapped in `ST_CollectionExtract(…, ST_Dimension(source) + 1)`, which keeps only the parts
at the source geometry's own dimension and yields an EMPTY geometry when the clip collapsed; the
outer `WHERE NOT ST_IsEmpty(…)` then drops the row instead of serving a different shape. Measured
2026-08-25: straddling polygons, crossing lines and a point ON the envelope boundary all survive
(each clips at its own dimension); only the collapse cases drop. The clip is computed once, in a
subquery, so filtering on it does not pay for a second intersection.

Two consequences of the clip worth knowing: the served geometry column holds **GeoJSON text**, not
WKB, and it is clipped to the request envelope when one is given. `rows` is deliberately untyped in
the contract (a schema per layer per kind), so this is a serving projection rather than a contract
change — but a caller diffing served bytes against the warehouse will see it.

`hive_partitioning=false` is not optional. With it on, DuckDB injects `layer`, `kind`, `zoom`,
`year`, `month` and `day` columns into every row, and `day` in particular would ride to the wire as
though the lane had published it.

## The extensions are in the IMAGE, never installed on a request path

`httpfs` and `spatial` are **not bundled in the DuckDB Python wheel** — `duckdb_extensions()` reports
both as `installed=false` with an empty install path. `INSTALL` downloads them from
`extensions.duckdb.org` into `$HOME/.duckdb`, and the runtime user's home is `/nonexistent`.
Reproduced in a container 2026-08-25 against the pinned base image: as uid 10001 the first
`INSTALL httpfs` raises `IOException: Can't find the home directory at '/nonexistent'`, and so does
every request after it. Invisible to every test, because a developer machine has them cached.

So the Dockerfile pre-installs them (`install_serving_extensions()`) into
`SERVING_EXTENSION_DIRECTORY`, and `load_serving_extensions()` only ever `LOAD`s, with
`autoinstall_known_extensions=false` so a broken image says so instead of silently downloading
mid-request. **`DUCKDB_EXTENSION_DIRECTORY` as an environment variable is not honoured** by DuckDB
1.5.4 — measured: it still resolved `$HOME/.duckdb` — so the directory arrives as
`SET extension_directory`, applied only when the directory exists so a developer machine falls back
to its own cache. The image runs `open_guarded_connection()` as the runtime user at BUILD time, so a
pre-install that lands where the session does not look fails the build rather than the first request.

## Every failure is a typed refusal before a surface maps it

`upstream-fault.ts` classifies `status >= 500` (and 429) as TRANSIENT, and the map retries a
transient fault once. An over-budget read escaping as a generic 500 therefore returns as a second
copy of the same oversized query against a process already at its ceiling. The core raises
`ServingRefusalError(code, message)` with no HTTP status. `interface/http/parquet_routes.py` owns the
complete code-to-status table and translates `duckdb.Error`, timeouts, and unexpected failures.
Other adapters preserve the same code and choose their own transport rendering.

`render_scalar` **fails closed** on any type it has no agreed rendering for. `str(value)` would have
served a Decimal, a list, a struct or a UUID as text under a type the contract never announced. Every
registered schema is scalar today — a census of `warehouse/schemas/` and `warehouse/parquet/schema.py`
2026-08-25 found only `string`, `float64`, `int8/16/32/64`, `bool_`, `date32`, `timestamp` and
`binary`, all of which render — so this is latent; `union_by_name` over a drifted object is how it
stops being latent. It bites test fixtures first: a bare `-116.2` in a `COPY … TO` is a DECIMAL to
DuckDB, which no lane can produce and this now refuses.

## Why a conflict and an unfinished export are not states

The contract has four states and none of them describes a day the warehouse cannot speak for:

- **`conflict`** (part files AND a governed-absence marker on one day) — an admin-only anomaly.
  Serving either half picks a side, so the plane refuses with **409**.
- **`incomplete`** (part files with no completion marker) — half an upload. Serving it puts a prefix
  of a release on the map; calling it `day_not_written` claims a gap that is not one, since the day
  demonstrably holds parts. The plane refuses with **503**, which is the honest code: an export in
  flight finishes, and a retry then succeeds.

**A window refuses as a whole when any day in it is conflicted or incomplete.** That is a real cost —
one mid-export day at the live edge fails a month-long window — and it is recorded here rather than
papered over, because the alternative is a false statement about a day. If the contract ever grows a
fifth state, this is the case that wants it.

The release resolver treats `incomplete` differently, and deliberately: an unfinished export is not a
published release, so resolution falls through to the previous one. A day named explicitly is not the
same question as "the newest release at or before this day".

## `truncated` carries two facts, both of them "there are rows we did not serve"

The budget (`serving.DAY_ROW_BUDGET`, `WINDOW_ROW_BUDGET`) is one. The other is a row the viewport
could not judge: two lanes (`sensors`, `water-gauges`) have nullable coordinates, and a row with no
position is excluded from a bbox read. Rather than dropping it silently — and rather than inventing a
field the contract does not have — the day reports `truncated: true`.

A window is answered by ONE scan with a shared budget, ordered by object key. Keys sort
chronologically **at day resolution**: `year=YYYY/month=MM/day=DD` is zero-padded, so a lexicographic
`ORDER BY filename` visits days in calendar order. The **part index is not padded** — `part-0`,
`part-2`, `part-10` sort `part-0, part-1, part-10, part-2` — so `part_keys_for_day` (which sorts
numerically on the parsed index) and `ORDER BY filename` (lexicographic) DISAGREE on the order of
parts *within* one day. Nothing here depends on intra-day order: truncation is attributed per day,
and a day is truncated as a whole. The day-level conclusion survives; the reason "every segment is
zero-padded" does not, and was wrong when first written.

Truncation therefore always falls at the LATE end: days before the cut are complete, the cut day and
every published day after it are marked `truncated`. A day whose rows were never reached is still
`published` with `rows: []` — "this day has rows and the budget ran out" — never `day_not_written`.

## Listings, and what is memoized

- `day` lists ONE month. Only a request landing outside every written month pays the whole-tier
  listing that separates `day_not_written` from `lane_never_written`; a non-empty month listing
  already proves the lane has been written.
- `window` lists the one or two months its range touches.
- `release` walks back year by year, bounded by `RELEASE_LOOKBACK_YEARS`, and stops at the first year
  holding a resolvable day.
- `coverage` is the expensive one — thirteen lanes × four tiers of whole-tier listings — and is the
  only thing memoized (`CoverageCache`, 120 s, under the client's own 300 s revalidation). Nothing
  else caches, so no row read can report a day as thinner than the warehouse holds.

The census carries three bounds a memo alone does not give it, all in `coverage.py`:

- **Single-flight.** `MAX_LISTED_KEYS_PER_REQUEST` bounds one listing; a memo bounds repeat work
  *after* the first answer exists. Without a lock, N cold page loads each started a full 52-listing
  walk. `CoverageCache` holds a `threading.Lock` — not `asyncio`, because `get` runs inside the
  route's pool thread — and a queued caller re-checks the memo before building. This mirrors the
  guard `environmental-read-model.getSliderCapabilities` already has.
- **An aggregate key budget.** `MAX_CENSUS_LISTED_KEYS` is spent across every listing of ONE census
  through `_BudgetedListing`; exhausting it refuses the whole census, because a partial one would
  report the lanes it never reached as absent.
- **Stale beats nothing.** A refresh that raises serves the previous census rather than turning a
  census the warehouse earned into a whole-map absence claim; `generated_at` states its age. When a
  refresh is already in flight and a previous census exists, callers are handed it immediately
  instead of blocking a pool thread. The FIRST census has nothing to fall back to, so it raises.

`lane_never_written` is asked of the RESOLVED TIER, not of the lane as a whole. Today the coarse
rungs (z9/z5/z0) are unbuilt for most lanes, so a z5 request honestly answers `lane_never_written` —
which is exactly what a slider needs in order not to mount an axis over a tier nobody wrote.

## Two things that are refused rather than widened

A bbox against a lane with no spatial extent (`calendar`) is a **409**, not a silently unbounded
read. A bbox against a lane whose registered schema declares the position columns while the written
objects do not carry them is a **503** — the state the `signal` base rung was in on 2026-08-25, mid
re-export. Both refusals exist because the alternative is answering a viewport with the whole world,
which is both a lie and the read that consumed the host.

**That probe is per OBJECT, not per union**, and the difference is the whole finding. `read_parquet`
with `union_by_name=true` reports the column set as the UNION over every object in the read, so a
`LIMIT 0` probe of the whole key set passes as soon as ONE object carries the columns. In the real
mid-re-export state — some days re-exported, some not — the probe passed and the predicate then
evaluated NULL for every object without the column and DROPPED its rows: a window answering
`state: "published", rows: [], truncated: false` for days that hold rows. `_unpositioned_rows` could
not catch it either, because `signal` and `vegetation` declare those columns `nullable=False`. The
probe is now one `parquet_schema(?)` call over the key set, which reports columns per file, and ANY
object missing them refuses the read naming that object's key. Reproduced 2026-08-25 against two
local parts: one row came back out of two.

## Known limits

- `asyncio.timeout` returns the caller; it does not stop a DuckDB query that already started, which
  runs to completion or to its memory ceiling in the worker thread. The worker keeps its admission
  slot until that actual completion, so later reads are refused at capacity rather than opening a
  session beside an abandoned query.
- `_unpositioned_rows` is skipped once the row budget is exhausted AND the caller reports one
  truncation flag for the whole read (the `day` and `release` routes), because the answer cannot
  change. A `window` attributes truncation per day, so it still pays a bounded existence probe —
  which is a `LIMIT 1`, not the `count(*)` over every part file it used to be.
- The per-object column probe reads the top-level names `parquet_schema` reports. A required column
  nested inside a struct would satisfy it; no registered schema has one.
- The census reports `kind=observed` only. The field stays on the wire so a forecast census can be
  added for a caller that asks for one, but the slider's capability rows resolve a layer name to the
  FIRST match, so a second row per lane would make which axis a layer draws depend on array order.
- A conflict day counts as WRITTEN in the census (it holds a release) even though serving refuses it.
  Reporting it as a gap would claim the lane never wrote that day.
- A `static_lookup` lane reports its version stamp as both bounds and NO ranges at all: its
  partition day is a version, not an observation day, so no day between two versions ever carried
  an obligation to exist (`lane_contract.py`). Ranging over them reported `watersheds` as one
  17-day gap when this was first run against the real warehouse.
- A `release_series` lane reports a gap only where a RELEASE was owed. A gap is a day that carried
  an obligation and is not there: `drought` publishes weekly, so a Wednesday was never owed a USDM
  map and reporting one as missing is a false claim about warehouse content — the same reasoning
  the `static_lookup` rung above already accepts. `coverage._owed_but_unwritten` walks each lane's
  own `cadence_days` from the days it did write, which for a `daily_series` (cadence 1) is every
  day and changes nothing. Measured 2026-08-25 before the rule: 138 releases produced 138 gap
  ranges, which at cutover paints the drought track absent six days in seven while `/release`
  serves those days by carrying the Tuesday forward.
- The two natures close their LIVE EDGE differently, deliberately. A `release_series` is not late
  until `publication_lag_days` after the next cadence step — USDM's Tuesday map is not missing on
  the Tuesday. A `daily_series` closes against TODAY and ignores its lag: every day up to today was
  owed an observation, the lag says when the driver gets to it rather than whether it is owed, and
  this is what the client's own `closeCoverageGapsAtLiveEdge` already assumes.
