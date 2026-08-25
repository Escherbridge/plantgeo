---
type: module-notes
---

# `interface/http/` — the `/api/v1/parquet` serving plane

Layer L4. Four bounded reads over the day-partitioned Parquet warehouse: `day`, `window`,
`release`, `coverage`. Pivot slices `d3` (routes) and `b1` (coverage).

## The contract is frozen elsewhere, and this directory obeys it

`tests/contract/wire_contract.py` is the declaration; `tests/contract/fixtures/*.json` are the nine
golden payloads; `src/lib/server/services/parquet-plane-client.ts`'s `WIRE` block is the TypeScript
half. **Nothing here may add a field.** `wire.py` spells every route segment and parameter name once
on the serving side and `tests/interface/test_wire_agreement.py` compares that spelling against the
frozen table, so a rename on either side fails the build.

Five behaviours the contract encodes, and where each one lives:

1. **All four states are HTTP 200 with a `state` field** — `parquet_routes._answer` only ever
   returns 200 for a resolved envelope; every non-2xx it emits is a `ServingRefusalError`, which is a
   statement about SERVING and never about content.
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

`duckdb_session.py` sets `memory_limit`, a `threads` cap, and `max_temp_directory_size='0GiB'`.
Spilling is DISABLED. That is a guard, not tuning: on 2026-08-24 an unbounded local DuckDB query
cross-joined ~140,000-vertex USDM polygons per output row and spilled until it consumed the host
(`analysis/AGENTS.md`). With spilling off the same query raises in about a second. **These routes run
DuckDB on request paths, so every session opened here carries the block, and the session is opened
per read rather than pooled** — a shared connection would let one oversized read spend the budget the
next one relies on.

**Clip before probing** is the companion discipline and lives in `warehouse_reader._projection`: a
geometry lane read with a bbox is served as `ST_AsGeoJSON(ST_Intersection(geom, envelope))`. Measured
here 2026-08-25 against the 2026-08-04 USDM release: the largest polygon falls from 124,676 vertices
to 6,151, with no precision loss inside the region actually requested. `ST_Simplify` was evaluated
upstream and rejected as the primary lever — it can move a boundary and flip a cell.

Two consequences of the clip worth knowing: the served geometry column holds **GeoJSON text**, not
WKB, and it is clipped to the request envelope when one is given. `rows` is deliberately untyped in
the contract (a schema per layer per kind), so this is a serving projection rather than a contract
change — but a caller diffing served bytes against the warehouse will see it.

`hive_partitioning=false` is not optional. With it on, DuckDB injects `layer`, `kind`, `zoom`,
`year`, `month` and `day` columns into every row, and `day` in particular would ride to the wire as
though the lane had published it.

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
chronologically (every segment is zero-padded), so the scan fills days in order and truncation always
falls at the LATE end: days before the cut are complete, the cut day and every published day after it
are marked `truncated`. A day whose rows were never reached is still `published` with `rows: []` —
"this day has rows and the budget ran out" — never `day_not_written`.

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

`lane_never_written` is asked of the RESOLVED TIER, not of the lane as a whole. Today the coarse
rungs (z9/z5/z0) are unbuilt for most lanes, so a z5 request honestly answers `lane_never_written` —
which is exactly what a slider needs in order not to mount an axis over a tier nobody wrote.

## Two things that are refused rather than widened

A bbox against a lane with no spatial extent (`calendar`) is a **409**, not a silently unbounded
read. A bbox against a lane whose registered schema declares the position columns while the written
objects do not carry them is a **503** — the state the `signal` base rung was in on 2026-08-25, mid
re-export. Both refusals exist because the alternative is answering a viewport with the whole world,
which is both a lie and the read that consumed the host.

## Known limits

- `asyncio.timeout` around `asyncio.to_thread` returns the request; it does not stop the DuckDB
  query, which runs to completion or to its memory ceiling in the worker thread. The ceiling is what
  bounds that, not the timeout.
- The census reports `kind=observed` only. The field stays on the wire so a forecast census can be
  added for a caller that asks for one, but the slider's capability rows resolve a layer name to the
  FIRST match, so a second row per lane would make which axis a layer draws depend on array order.
- A conflict day counts as WRITTEN in the census (it holds a release) even though serving refuses it.
  Reporting it as a gap would claim the lane never wrote that day.
- A `static_lookup` lane reports its version stamp as both bounds and NO ranges at all: its
  partition day is a version, not an observation day, so no day between two versions ever carried
  an obligation to exist (`lane_contract.py`). Ranging over them reported `watersheds` as one
  17-day gap when this was first run against the real warehouse.
- A `release_series` lane reports the days BETWEEN its releases as gaps, because the contract's
  rule is "a day counts as covered when any published tier HOLDS it" and a Wednesday holds no
  USDM map. That is truthful and noisy: `drought` censuses 211 gap ranges over four years. The
  `nature` field is on the wire so a client can read those as "between releases" rather than as
  missing data, and the `release` route is what serves a Wednesday by carrying forward. If the
  slider ends up graying out six days in seven for drought, that is the decision to revisit --
  in the contract, not here.
