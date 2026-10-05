# Layer L3: Planes

## Responsibility
Domain execution planes that bind method algorithms and pipeline acquisition outputs into warehouse persistence.

## Transitional botanical species information

`botanical_species_information.py` is a bounded, nonspatial exception while an immutable botanical
profile release is unavailable. It reads only the existing modeled `agri.species` row selected by
exact UUID and approved `agri.companion_relationships`; no name join, GIS observation, environmental
relation, migration, or writer is part of the plane. The `reviewed_authoring_database` source label
names the governed authoring surface, not approval of each legacy field. Populated values—including
Boolean defaults—remain `unverified_authoring`; nulls remain explicit `unknown/not_reported`.
Recommendation, training, and final published serving still require an independently reviewed,
release-pinned Parquet profile.

## Dependency Rules
- **May import**: `foundation` (L0), `method` (L1), `warehouse` (L1), `pipeline` (L2).
- **May NOT import**: `interface` (L4).

## Two soil-survey read paths in one file (port slice S3)

`soil_survey.py` holds two unrelated readers that happen to answer the same question ("what soil
is at this location") over two unrelated storage shapes, kept in one module rather than split
because a caller reaches for "the soil-survey plane" as one name, not two:

- The functions ABOVE its "Admitted release read path" banner read the day-partitioned
  `SOIL_SURVEY_STREAM` Parquet lane (`resolve_soil_survey_release`, `soil_survey_at_point`, the
  dependency-free WKB point-in-polygon decoder). That lane has no bounding-box column and no
  release/shard/candidate model; it is what this file was before the port.
- The functions BELOW that banner read `foundation.soil_survey.release`'s content-addressed
  `Release` / `ShardRef` / `Candidate` / `Part` objects: an operator explicitly stages shards,
  writes a release index, and pins its SHA-256 in `config.py::ssurgo_admitted_release_sha256`
  before this path ever serves anything (`pipeline/direct/soil_survey/AGENTS.md`, "Operational
  entry point"). This is the path the current SSURGO route (`interface/http/soil_survey.py`)
  actually calls.

Do not merge the two: the old lane's schema genuinely has no bbox columns (its own docstring
explains why that is an honest trade), while the new path's entire selection strategy DEPENDS on
one (see the next section). A future migration to the platform's `static_lookup` layout
(`.omc/research/merge-20260927/soil-survey-port-plan.md` §4) is expected to retire the old lane
outright rather than reconcile the two schemas.

### SSURGO overview below z13

`load_soil_survey_overview` / `render_soil_survey_overview` serve the overview the operator derived
from the admitted release (`pipeline/direct/soil_survey/overview.py`; rationale and publish recipe in
that directory's `AGENTS.md`, "Overview below z13"). `decode_overview` refuses a file stamped for any
release but the pin, so re-pinning a new release never serves a stale overview -- it serves "zoom
in" until that release's own overview is published. `select_overview_cells` draws the finest rung
the request's ladder tier allows (`OVERVIEW_FLOOR_DEGREES_BY_TIER`: z9-12 -> 0.025, z5-8 -> 0.05,
z0-4 -> 0.2 degrees) whose viewport fits `MAX_OVERVIEW_CELLS`, else the next coarser; if even 0.2
overflows it keeps the cells nearest the view centre and says `truncated`. The viewport's east and
north edges are exclusive, so a cell that only touches the edge is not drawn. Features carry the
legacy aggregate shape (`aggregated`, `drainageClass`, `mapUnitCount`, `hydricFraction`) that the
web hover and soil panel already caption as averages, plus `cellDegrees`, `dominantShare`,
`mappedShare` and `geometryRepresentation: "overview_cell"`; `spatialCoverage.viewportAreas` is
empty because a cell is not attributed to survey areas.

`render_soil_survey_status` describes an already verified admitted index independently of the
old lane census. It reports the actual source vintage, capture clock and published/pending
survey-area counts, preserving partial release coverage. These fields authorize a static
publication label, not an observed-day range, complete regional coverage, or successful reads
of every geometry object. Viewport reads retain their own manifest and part verification.

## Point lookups: bbox in SQL, exact ring in Python (owner Q4, 2026-09-27; revised, review finding 2)

The admitted-release path never runs a GEOS predicate (`ST_Intersects`, `ST_Covers`) against
`geometry_wkb` inside `sql/planes/ssurgo_{viewport,point}.sql` or the Python shard/part-pruning
stage. Viewport membership and both stages' PREFILTER read only the four
`bbox_west/south/east/north` columns the schema carries per row (`warehouse/schemas/soil_survey.py`).

This is a direct consequence of the owner's Q4 decision -- "repair else quarantine label and serve
always": a delineation whose ring could not be repaired is still served, carrying
`geometry_quality: "invalid_unrepaired"`. A GEOS predicate over that ring would either raise or
silently answer "does not intersect/does not contain", and either way "always serve" would quietly
become "serve unless the geometry is bad" -- the one outcome Q4 explicitly rules out. This still
governs the VIEWPORT query (`ssurgo_viewport.sql`) in full: bbox overlap is the only test, by
design, because a viewport answer is "everything near here", not "the one thing at this exact spot".

A POINT lookup is a different question -- "what soil is AT this exact location" -- where bbox-only
selection was a real correctness bug, not an accepted trade: dense/riparian SSURGO coverage
routinely nests more than `DEFAULT_MAX_POINT_MATCHES` bboxes over one point, so a bbox-only cap
could drop the one delineation whose ring genuinely contains the point while keeping neighbours that
merely reach it (review finding 2, 2026-09-27). `ssurgo_point.sql` is therefore STAGE 1 only -- the
same cheap bbox prefilter as the viewport query, selecting `geometry_wkb` itself (not excluding it)
so there is something to test. STAGE 2, `planes.soil_survey._filter_point_candidates_by_ring`, runs
in Python and applies `wkb_polygon_contains_point` -- the SAME dependency-free ray-cast the
day-partitioned point-lookup lane above already uses -- to every stage-1 candidate: this is an exact
geometric test, but never a GEOS predicate, so Q4's rule still holds. A row whose `geometry_wkb`
cannot be decoded is KEPT rather than dropped, matching Q4's "always serve, label don't hide" stance
even where this lane's own decoder cannot fully parse a ring. `ST_GeomFromWKB` and `ST_AsGeoJSON`
are still used freely everywhere in this path -- they only ever RENDER a stored ring for the
response, never test it, so they carry none of this risk.

## Admitted release read path: caps, caches and the DuckDB slot (F10)

`gather_admitted_soil_survey_viewport` does every object-store read and digest check (the release
index, each touched shard's manifest, each touched part's bytes) as a plain function with no
DuckDB connection open. The R12 "z13 detail ceiling" (`MAX_SOIL_SURVEY_VIEWPORT_SQUARE_DEGREES`) is
checked FIRST, before any shard's bbox is even compared, so a whole-region viewport is refused
without touching a single manifest -- there is no coarser rung to answer it with anyway (Q1).
`MAX_PARTS_PER_VIEWPORT` and `MAX_VIEWPORT_BYTES` (`foundation/soil_survey/release.py`) are then
checked twice: once against each part's DECLARED `blob.byte_count` before any byte is fetched, and
again against the DECOMPRESSED Parquet row-group metadata as each part is actually read, because a
compressed part can decompress far larger than its staged size. Only `run_admitted_soil_survey_query`
-- registering the already-assembled Arrow table and running one parameterised `SELECT` -- is meant
to run inside `parquet_ops.duckdb_session.run_serving_read`'s bounded slot; `interface/http/
soil_survey.py` is what keeps the two apart, so a slot is never held while this module is still
doing bucket I/O.

`interface/http/soil_survey.py` bounds how many requests can run that bucket I/O AT ONCE with a
module-level `asyncio.Semaphore`, sized to `parquet_ops.duckdb_session.SERVING_MAX_CONCURRENT_READS`
for consistency with the DuckDB slot it sits in front of (review finding 5) -- the object-store
phase is otherwise unbounded concurrency-wise, unlike the DuckDB phase downstream of it.

Two gaps from the plan's §1a row are DEFERRED, not fixed here (review finding 5's remaining asks):
a single-flight manifest/release cache keyed by SHA, and true mid-read cancellation once
`_READ_DEADLINE_SECONDS` elapses (`asyncio.timeout` cannot cancel an `asyncio.to_thread` worker; a
real cancellation token would have to be threaded through every `storage.read` call in this module).
Every request currently re-fetches the release index and every touched shard's manifest fresh, and
a slow read past the deadline still finishes its current object read in the background even though
the caller already got its 503. Both are noted as a deviation, not concealed, and are a reasonable
follow-up rather than a blocker: `Release.__post_init__` already caps a release at
`MAX_RELEASE_SHARDS = 32` shards, so a single request's worst case is a FIXED 32 manifest loads
(cheaper still once the R12 ceiling above prunes most of them), and `BotoAvailabilityStorage.
from_credentials` performs no network I/O building the client -- it is built once per request the
same way `interface/http/parquet_routes.py` already does it elsewhere in this service, not a new
per-request cost this module introduced.

## The zoom axis: one rule, no exceptions

Every PUBLIC function in this directory takes a `requested_zoom: int` -- the map zoom a viewport is
actually at -- and resolves it exactly once through `foundation.parquet.zoom.serving_zoom_tier`.
Every PRIVATE helper takes the already-resolved `zoom: ZoomTier`. The two types are the signal: an
`int` has not been resolved yet, a `ZoomTier` has, and nothing in between exists.

A plane resolves rather than demanding a tier because `serving_zoom_tier` walks DOWN -- z11 reads the
z9 rung -- and that direction is a correctness rule, not a rounding preference (`zoom.py`'s module
docstring: rounding up claims a resolution the writer never generalised to). Every caller
re-deriving it is one caller away from rounding the other way. Resolution is idempotent, so a caller
that already holds a tier may pass it straight in: every tier is a legal request that resolves to
itself.

Three consequences worth stating, because each has a silently-wrong alternative:

1. **One call, one rung, listing and scan alike.** When a function lists to discover a day and then
   scans to read it, both name the SAME resolved tier. Splitting them yields a release day that is
   real and rows that are not the ones it names.
2. **No "all tiers" mode, and no default.** A default would decide the axis quietly, and the mistake
   surfaces as geometry at the wrong resolution rather than as an error. A blended read is not
   expressible here: no signature accepts more than one zoom.
3. **The answer says which rung answered.** Where a module already names what answered
   (`answered_by_snapshot_day`, `valid_date`, `release_day`), it names the tier beside it. A z11
   request served from z9 got a different resolution than it asked for, and that is the same class
   of fact as being served a three-day-old snapshot.

The tier is NOT stamped as a data column anywhere. `kind` is, because Polars' Hive injection is
inconsistent between empty and non-empty scans and because callers concatenate two kinds' frames; no
caller ever builds a frame spanning two tiers, so a per-row tier stamp would disambiguate a case that
cannot arise while adding a column no registered schema has.

`pipeline/validation/*` deliberately does NOT follow this rule -- see that directory's own note.
