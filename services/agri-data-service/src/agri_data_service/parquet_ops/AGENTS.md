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
5. **Coverage proves each physical rung independently** — `(layer, kind, zoom)` is the row identity;
   a mutable rung is listed independently; an immutable monthly product may reuse its exact z13 day
   set only after its manifest proves identical checkpoint months and part counts at every rung. A never-written rung reports `null` bounds. The census
   includes the 11 direct slider lanes plus the 15 schema-backed dedicated climate/soil prefixes.
   Catalogue products without a serving schema stay unregistered and therefore cannot be proven.

## Coverage authority: the index answers, the census is the fallback

RUNBOOK "Availability artifact contract"; `conductor/code_styleguides/layer-lanes.md` §4a. The
capability/slider census used to LIST every day prefix of every lane on every cold request. It no
longer may. `availability_coverage.py` answers a lane from **one pointer GET** of
`<lane-root>/availability/_LATEST.json` plus **one bounded Parquet GET** of the
`generation=<sha>/availability.parquet` that pointer names, and never touches a `WarehouseListing`.

`lane_root` is `foundation.parquet.paths.stream_prefix(layer, kind)` minus its trailing separator —
`layer=signal/kind=observed`. It is derived from the SAME helper the warehouse writes through and the
census lists through, deliberately: a second spelling here would make a published index invisible to
the reader and look exactly like "not bootstrapped yet".

**One evidence source per lane, chosen by `Settings.parquet_coverage_authority`
(`PARQUET_COVERAGE_AUTHORITY`).**

- `availability` — every TIME-BEARING lane is served from its index, and so is every snapshot
  product's forward half. A lane that cannot prove itself is WITHHELD: it stays on the wire with
  null bounds, empty ranges and one of four `withheld_reason` values, and offers no selectable days.
- `census_until_bootstrap` — **TRANSITIONAL, and it is deleted, not kept.** Per lane: a valid index
  wins and that lane never lists again; a lane whose pointer object is ABSENT falls back to the
  existing census and is labelled `coverage_authority: "census"`. A malformed or checksum-invalid
  index is withheld in this mode too — falling back on corruption would let the scan the artifact
  exists to retire quietly re-prove a lane whose bytes disagree with their receipt. **Delete this
  mode, its `Literal` arm and this bullet once every lane's production bootstrap receipt is
  recorded**; until then it is the bridge, and after then it is a way to silently resume listing.

**A `static_lookup` stays on the census under BOTH policies.** §4a gives an index to every
TIME-BEARING lane, and a version stamp is not a time axis, so these lanes will never publish one. It
used to be withheld under `availability` to keep the mode's zero-LIST promise literally true; that
bought three fewer listings by deleting three published reference sets from coverage entirely, which
is a worse answer than the listing. The three of them are the whole remainder under `availability`;
every daily and release lane costs one pointer GET and one generation GET.

**A lane that was bootstrapped and then lost its pointer is WITHHELD, never censused.** Falling back
silently on `availability_missing` cannot distinguish "this lane has no index yet" from "this lane's
mutable head is gone", and the second re-opens the whole-stream listing the artifact retired —
forever, on a green tick. The discriminator is one GET at the deterministic
`<lane-root>/availability/bootstrap/_BOOTSTRAPPED.json` marker the bootstrap writes beside its
content-addressed receipt. A marker is immutable, so a positive answer is cached for the process
life and a negative one never is. Every census fallback logs at WARNING with the lane root, because
the bridge is meant to empty rather than to persist.

**A pointer frozen beyond tolerance is withheld `availability_stale`.** `read_latest_availability`
receives a `required_source_ceiling` of the lane's own allowed ceiling minus
`cadence_days + publication_lag_days + AVAILABILITY_STALE_GRACE_DAYS`: one whole publication period,
plus grace. The ceiling only advances when a day is actually published, so a lane whose upstream
skipped one issue is merely quiet, while a lane that has missed a period AND the grace has a
publisher that stopped. Too tight a value greys out healthy lanes on one missed cron tick, which is
strictly worse than serving a horizon a few days old — that horizon is itself on the wire as
`source_ceiling_day`, so a client can judge it. The test is applied where the pointer is FETCHED and
reused for at most one `POINTER_REVALIDATE_SECONDS`, within which the lane's allowed ceiling cannot
have moved by a day.

Everything the four rung rows say comes from `coverage.close_lane_coverage`, the same closing
function the census uses, fed `LaneDays` built from the index instead of from object keys. What
changes is the HORIZON: the census closes against today, an availability lane closes against its own
`source_ceiling`. That is why a lane whose source lags a week no longer reports a week of phantom
gaps — and why `source_ceiling_day` rides on the wire beside the envelope's `evaluated_through_day`,
which is only when the answer was computed.

That ceiling is `pipeline/parquet/lane_ceiling.allowed_source_ceiling`, the SAME function the
publisher used to declare it, so it has already subtracted the lane's publication lag. The
availability path therefore passes `horizon_already_lag_adjusted=True` and `_owed_but_unwritten`
does not charge the lag a second time; charging it twice would silently hide one whole lag period of
a release lane's real gap tail. The census keeps passing today, unadjusted, and keeps charging the
lag itself — a USDM Tuesday map is not late on the Tuesday.

Every rung reports the lane's **selectable** days — days whose whole authoritative rung set agrees on
one terminal state — and not that rung's own rows. The intersection is a subset of each rung, so no
row over-claims, and a slider can never mount an axis at z13 over a day z0 cannot draw.

Two bounds are not tuning. `POINTER_REVALIDATE_SECONDS` (60 s) is the entire staleness budget of the
availability path, because `AvailabilityStorage.read` is an unconditional GET with no `If-None-Match`
to revalidate against. `MAX_CACHED_GENERATION_BYTES` (8 MiB) bounds what a generation may cost in
memory: the publication contract allows 256 MiB per generation, and one of those held per lane is
the 2026-08-24 ceiling incident again in a different costume. A generation key carries its own
digest, so a cache hit can never be a stale hit and the cache needs no expiry.

An availability read that fails with anything OTHER than the four refusals — a botocore fault, a
timeout — propagates and refuses the whole coverage answer. The four `withheld_reason` values mean
exactly what they say and must not become a bucket for transport faults; the census this replaces
fails the whole answer for the same reason.

`snapshot_products.SNAPSHOT_PRODUCTS` is the single immutable-product allowlist. Add a product only
after its production `manifest.json` and `_COMPLETE` are final. Dew point entered the allowlist only
after its final output receipt was pinned; no product may be registered with a guessed digest.

### `forward_first_day`: a product may be frozen at one end only

The six NASA POWER climate products are closed BELOW `pipeline/direct/climate/products.py`'s
`CLIMATE_DIRECT_WRITER_START_DAY` and live at and above it — the direct writer publishes those days
into the ORDINARY lane layout, `layer=<slug>/kind=observed/zoom=NN/year=/month=/day=/`, under a
completion marker rather than under this module's receipt chain. The constant is IMPORTED from the
writer, never restated: a reader boundary that drifted from the writer's start day would silently
strand every day between the two.

Three consequences, each of which was a live defect before the boundary existed:

- **Routing is day-aware.** `serves_from_snapshot(layer, day)` replaces `layer in PRODUCT_BY_LAYER`
  in every HTTP and CLI adapter. The layer-only test answered `day_not_written` for days sitting in
  the bucket. A window is routed on its FIRST day and straddles the boundary internally.
- **Coverage unions both halves, and the forward half is AUTHORITY-AWARE.** Under
  `census_until_bootstrap` it is one listing of the product's live lane prefix, labelled
  `coverage_authority: "census"` and logged once per `SnapshotCoverageCache` TTL, because that is
  the bridge's real per-request cost. Under `availability` a request-path LIST is exactly what the
  index exists to retire, so the forward half comes from the product's OWN availability index at the
  ordinary `availability_lane_root(layer, "observed")` — and when no index exists the forward half
  is WITHHELD (`availability_unpublished` on the product's coverage row) rather than listed. The
  port (`ForwardAvailabilityPort`) is declared in `snapshot_products.py` and implemented in
  `availability_coverage.py`, that way round because `coverage.py` already imports
  `snapshot_products` and an import back at module scope would close a cycle. The manifest-equality
  check stays scoped to the closed half, because the manifest is silent above the boundary by
  construction — and a manifest day AT OR ABOVE the boundary now refuses the whole product
  (`snapshot_manifest_conflict`), since a day excluded from the equality check and then unioned into
  the answer is an unverified claim published as closed evidence.
- **A forward governed absence is a governed absence.** It is admitted into
  `governed_absence_ranges` and subtracted from `gap_ranges`. Reporting it as a hole told a client
  to keep asking for a day the lane had already settled.
- **`source_ceiling_day` is a maximum, not the manifest's last day.** Once a writer publishes past
  the frozen edge, reporting the manifest's day would put `latest_day` above the lane's own ceiling,
  which reads as a lane serving days its source cannot have produced.

A forward day's ROWS come back through the ordinary `DuckDbRowReader`, not through the snapshot's
pinned-schema reader, so a forward day and a closed day of the same layer cannot disagree about
columns, viewport support or budgets. It costs ONE listing of one day prefix; a fully frozen product
lists nothing at all.

**One row order across the boundary.** The closed half's SQL ends `ORDER BY cell_longitude,
cell_latitude`; `DuckDbRowReader` orders every lane's day by source key, so a window straddling
`forward_first_day` returned two differently-ordered halves in one answer.
`_in_closed_half_order` re-sorts the forward rows rather than changing the reader, which serves
twelve other lanes whose grain is not a cell. THE TRUNCATION BOUNDARY IS NOT RE-SORTED and cannot
be: the reader's `LIMIT` selects in ITS order, so a truncated forward day returns a source-key
ordered SUBSET presented in lon/lat order. That is what `truncated` on the envelope is for — a
partial day is declared partial, and its last row is not the day's last row.

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

`render_scalar` **fails closed** on any type it has no agreed rendering for. Both the mutable DuckDB
reader and immutable snapshot reader wrap only columns whose registered Arrow schema declares a
list, so snapshot-lineage arrays render recursively while a raw list from an untyped or drifted row
remains refused. `str(value)` would still
serve a Decimal, struct or UUID as text under a type the contract never announced, and remains refused.

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

A direct-partition window is answered by ONE scan with a shared budget, ordered by object key. Keys sort
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
Immutable monthly snapshot windows retain exact-day filtering, so they may use one bounded query per
day rather than one union scan, but they spend the same single `WINDOW_ROW_BUDGET` across the closed
range and propagate truncation from the first cut published day through every later published day.

## Listings, and what is memoized

- `day` lists ONE month. Only a request landing outside every written month pays the whole-tier
  listing that separates `day_not_written` from `lane_never_written`; a non-empty month listing
  already proves the lane has been written.
- `window` lists the one or two months its range touches.
- `release` walks back year by year, bounded by `RELEASE_LOOKBACK_YEARS`, and stops at the first year
  holding a resolvable day.
- `coverage` is the expensive one — one whole-stream listing for every mutable direct/product lane,
  plus manifest-bound immutable snapshot evidence. Closed daily snapshots derive exact days from
  bound keys/ranges; closed monthly products use declared contiguous ranges. The older NASA air and
  dew manifests predate an explicit day-count field, so they instead prove the same range from their
  signed unique `(support, signal, unit, cell, day)` grain, one bound z13 row count per month, and the
  fixed 397-cell `nasa-power-0.5-degree` lattice. The 397-cell cardinality is the reviewed historical
  plan and `agri.spatial_cell` contract recorded in `docs/lanes/weather-observations.md`; the serving
  proof additionally requires the manifest's exact grid name and rejects any month not equal to
  `calendar days x 397`. A missing day cannot be hidden by a duplicate because the signed grain is
  unique. It is the
  only mutable census memoized (`CoverageCache`, 120 s, under the client's own 300 s revalidation).
  Immutable checkpoint evidence is process-cached by backend namespace, product roots, and the exact
  manifest SHA after every caller rebinds `manifest.json` to `_COMPLETE`; selected Parquet payloads
  are still rehashed before each exact day/window read.

Cold mutable stream listings use exactly three workers. Immutable product evidence uses four outer
workers, while each product's manifest-bound checkpoint/marker verification retains its existing
16-worker ceiling; output is collected in allowlist order and a failure still withholds only that
product. Coverage performs no DuckDB query and no serving-Parquet GET. Each mutable
stream is consumed as an iterator and charges the one locked 600,000-key census budget before retaining
a key, so concurrent listings cannot multiply the aggregate memory allowance. Measured against
production R2 on 2026-08-28, the pre-product 52-prefix walk covered 121,386 keys in 16.27 s at this
ceiling. The slider census now makes 26 stream-prefix calls (11 direct plus 15 products) and emits
104 independent rung rows while keeping the same aggregate key refusal and bounded worker fan-out.
The HTTP adapter additionally terminates any cold census at 29 seconds, below the client's 30-second
budget. Tests prove both declared and fixed-lattice monthly coverage open no Parquet, and that cold
product verification reaches exactly the four-worker ceiling; only a deployed probe can establish
production latency.

Snapshot coverage isolates publication evidence per product. A missing, unbound, schema-drifted, or
unreadable product contributes no rung rows, because null-bounded rows would falsely call it
never-written. The internal census retains the exact typed withholding and the HTTP adapter logs it;
healthy product rungs remain in the frozen wire response. Unexpected programming faults still abort
the census instead of being disguised as product evidence.

Snapshot serving keys are never reconstructed from path conventions. Product manifests bind exact
checkpoint or verification-marker JSON receipts; those verified bytes bind exact rung
key/byte/SHA receipts for monthly and daily layouts.
Soil-temperature additionally requires the manifest's base and tier checkpoint receipts to cross-bind
before exposing their four rungs. An exact day/window GET hashes each selected Parquet object before
DuckDB opens it. Coverage verifies the manifest/checkpoint chain and uses its receipt metadata without
GETting every serving Parquet object; downloading the full multi-product snapshot on each 120-second
cold census would violate the 29-second server budget. Cold checkpoint/marker GETs use a bounded
16-worker pool. HTTP day/window callers resolve that single-flight evidence before DuckDB admission,
so cache waiters occupy no serving slots; only evidence-bound row queries enter the three-slot pool.

The census carries three bounds a memo alone does not give it, all in `coverage.py`:

- **Single-flight.** `MAX_LISTED_KEYS_PER_REQUEST` bounds one listing; a memo bounds repeat work
  *after* the first answer exists. Without a lock, N cold page loads each start a full stream-prefix
  walk. `CoverageCache` holds a `threading.Lock` — not `asyncio`, because `get` runs inside the
  route's pool thread — and a queued caller re-checks the memo before building. This mirrors the
  guard `environmental-read-model.getSliderCapabilities` already has.
- **Metadata census outside query admission.** The HTTP payload cache uses an `asyncio.Lock`; cold
  waiters own no DuckDB session and no bounded serving slot. The one winner builds the merged census
  from object metadata in a worker thread, and waiters re-check and reuse its payload after the lock
  opens. Coverage never enters `run_serving_read`.
- **An aggregate key budget.** `MAX_CENSUS_LISTED_KEYS` is spent across every listing of ONE census
  through `_BudgetedListing`; exhausting it refuses the whole census, because a partial one would
  report the lanes it never reached as absent.
- **Expired evidence proves nothing.** Once the TTL passes, callers block behind the one refresh,
  then re-check for its fresh result. A failed refresh is propagated to every queued caller; the
  previous census is retained only so requests whose own `now` is still inside its TTL can reuse it.
  This keeps a listing outage from silently publishing a capability on stale readability evidence.

Every census also freezes `evaluated_through_day`, the UTC date through which its cadence, lag, and
absence rules ran. It is distinct from the diagnostic `generated_at` instant so a downstream cache
that crosses UTC midnight can fail closed instead of treating yesterday's silence as today's proof.

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
- A `release_series` lane reports the bounded days its reader can answer, not only raw publication
  dates. Historical weekly drought releases and governed absences carry from Tuesday through Monday.
  The latest stored publication or governed absence uses the reader's 14-day live allowance, capped
  by the evaluation day; once a later status lands, the older one immediately reverts to six-day
  historical carry. Every day outside those intervals is a gap. `coverage._owed_but_unwritten`
  closes that full uncovered interval, while a `daily_series` (cadence 1) continues to owe each
  observation day directly.
- A conflicting drought status is retained by the census even though it is neither published nor a
  governed absence. `/release` refuses that conflict as the newest status until a later clean status
  supersedes it, so coverage marks the same conflict-through-supersession interval as a gap instead
  of carrying the older release green.
- Live-edge closure follows the serving reader. Direct drought uses the bounded 14-day latest-status
  rule above. Other `release_series` lanes remain cadence obligations settled after
  `publication_lag_days`. A `daily_series` closes against TODAY and ignores its lag: every day up to
  today was owed an observation, and the lag says when the driver gets to it rather than whether it
  is owed. This is what the client's own `closeCoverageGapsAtLiveEdge` already assumes.
