---
type: lane-contract
slug: watersheds
horizon: none
---

# watersheds lane

Source-of-truth spec for the `watersheds` layer lane, one of the eleven layers
named in `conductor/RUNBOOK.md` §0.24.2 (`conductor/RUNBOOK.md:3336-3348`) and
governed by `conductor/code_styleguides/layer-lanes.md`. This document does
not assert any Parquet path layout, filename, or column list — that contract
is being written concurrently by another agent. Where the repo does not
establish a fact, it is marked `UNVERIFIED` with what would confirm it.

**This is one of the two layers the owner explicitly named STATIC and exempt
from revalidation** (`conductor/RUNBOOK.md:2943-2944`, §0.21.5, alongside
`soil-survey`). **Settled — recorded here, not re-litigated.**

## 1. Source system

- **Publisher**: USGS — the Watershed Boundary Dataset (WBD), HUC12 tier,
  served through the **NHDPlus_HR** ArcGIS MapServer rather than a WBD-branded
  endpoint.
- **Endpoint**: `https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer/12/query`
  — **layer 12** (`WBDHU12_LAYER_ID`, `services/agri-data-service/src/agri_data_service/ingest/watersheds.py:61-64`).
  Explicitly **not layer 2** (`NHDPoint`), which returns an unlabelled point
  cloud carrying no `huc12` attribute at all — verified against the service
  catalog and recorded as a named mistake to avoid in both the Python adapter
  (`watersheds.py:58-60`) and its pre-existing TypeScript sibling
  (`src/lib/server/services/hydrosheds.ts:8-16`).
- **Query mechanism**: two-phase, id-batched, **not offset paging**. Phase 1 —
  one `returnIdsOnly=true` request for every OBJECTID intersecting the bbox
  (`fetch_watershed_object_ids`, `watersheds.py:227-237`; measured 9,396 ids
  over the PNW envelope in a few seconds because the response carries no
  geometry). Phase 2 — geometry fetched in explicit batches of 200
  (`WBDHU12_BATCH_SIZE`, `watersheds.py:66-77`), addressed by named OBJECTID
  rather than `resultOffset`. **Offset paging genuinely does not work here**:
  asking this layer to sort while returning geometry answers HTTP 500 over the
  PNW envelope; the identical query with `returnGeometry=false` sorts fine
  (`watersheds.py:69-72`, corroborated by the id-only phase succeeding). This
  is a real, measured upstream constraint, not a style preference — any
  Parquet-era fetcher reusing this endpoint must keep the two-phase shape.
- **Auth**: none. Requests carry only `Accept: application/json`
  (`watersheds.py:209-216`; identically in `hydrosheds.ts:72-80`). No API key
  or credential env var exists for this source — the only watersheds-specific
  env var is `WATERSHEDS_LAYER_ID`, which overrides the **destination**
  `geo.layers` row name, not an upstream credential (`watersheds.py:44-51`),
  and it is not documented in `docs/env-vars.md` (no `WATERSHED` hit found
  there). **UNVERIFIED**: whether NHDPlus_HR enforces an undocumented rate
  limit beyond the measured payload/timeout ceilings below.
- **Payload/timeout ceilings, measured**: `WBDHU12_BOUNDS` = 32 MB / 120 s per
  page (`watersheds.py:79-82`); 500 basins at full precision measured 12.4 MB,
  which is why the batch size is 200 (~5 MB/request) rather than the service's
  own 2,000-record `maxRecordCount` (`watersheds.py:66-77`). Coordinates are
  rounded to `geometryPrecision=6` (~0.1 m, far finer than the 1:24,000 source
  was digitized at), cutting payload by ~40% — the same value and reasoning
  the TypeScript proxy independently arrived at (`watersheds.py:84-86`,
  `hydrosheds.ts:65-69`).
- **Sequential, not concurrent, fetching is deliberate**: `fetch_watersheds`
  walks id batches one at a time because firing archive-style requests back to
  back has been measured elsewhere in this repo to exhaust connections (169 of
  298 windows lost to `ConnectError`), and this source runs rarely enough that
  a few extra minutes cost nothing (`watersheds.py:240-246`).
- **Licensing**: **not directly declared for this adapter.** No
  `license_name`/`license_url`/`licence_identifier` field is carried anywhere
  in `ingest/watersheds.py` or on the `geo.layers` row it writes to (contrast
  `ingest/mtbs.py:147-154`, which does carry `MTBS_LICENSE_NAME` /
  `MTBS_LICENSE_URL` through to `agri.source_release.license_snapshot`).
  Circumstantial evidence for the license class exists but is **scoped to a
  different, unrelated planned adapter**: `source_manifests.py:312-324`
  records `"usgs-3dhp-wbd-hydrography"` (`adapter_status="planned"`, part of
  the North America intervention-source matrix, not this lane) with
  `licence_identifier="US Public Domain"` and
  `attribution="USGS 3D Hydrography Program / Watershed Boundary Dataset"` —
  the same upstream dataset family, different consumer. USGS/WBD data is
  conventionally U.S. federal public domain, but that is general knowledge,
  not something `ingest/watersheds.py` itself states or enforces. Confirm
  against USGS's current WBD terms before publishing any attribution string in
  the Parquet export; `WATERSHEDS_PROPERTY_SOURCE = "USGS NHDPlus HR WBDHU12"`
  (`watersheds.py:42`) is the only source label actually written today, and it
  is a provenance label, not a license declaration.
- **A second, independent client hits the same upstream**: `hydrosheds.ts`
  (`src/lib/server/services/hydrosheds.ts`) is a **live proxy**, unrelated to
  the persisted lane, cached in Redis for one hour and capped at
  `MAX_WATERSHED_BBOX_SQUARE_DEGREES = 1` square degree
  (`hydrosheds.ts:24-30`). It predates the persisted adapter and was **not
  retired** when persistence shipped — see trap 2 in §5. Any accounting of
  "how much load does this layer put on NHDPlus_HR" must count both.

## 2. Cadence

**Not a periodic pull.** This is a boundary-set republication, not a
time-series sample.

- **Upstream cadence** (general knowledge, corroborated at arm's length by
  `source_manifests.py:322`, which describes the same WBD/NHDPlus_HR family
  under a different manifest entry): *"quarterly (3DHP) / annual (WBD); legacy
  NHD maintenance stopped 2024."* Treat as directionally informative, not a
  verified declaration for this specific lane.
- **`ingest-watersheds`'s own docstring is explicit and is the load-bearing
  statement here**: *"Run once, then only when USGS republishes the WBD...
  there is no backfill verb for this source because there is no series to
  walk"* (`services/agri-data-service/src/agri_data_service/ingest/commands.py:267-272`).
- **Nothing invokes it automatically.** The verb is registered
  (`ingest-watersheds`, `commands.py:264-267`, listed at `commands.py:1305`)
  but is **absent from `ingest-all`'s job list** and there is **no
  `infra/cron-watersheds` directory** — confirmed: `infra/` holds only
  `cron-ingest/`, `cron-mtbs/`, `cron-soilgrids/`
  (`conductor/RUNBOOK.md:879-882`). `ingest_all`/`_run_all`
  (`commands.py:484-497`) runs a fixed set of sources that does not include
  this one.
- **Measured production reality**: exactly **one** load day exists,
  2026-08-07, all 9,396 rows (`conductor/RUNBOOK.md:872`, `:3593`). The local
  run log confirms the shape of that one run
  (`services/agri-data-service/.agri-local-runs/logs/watersheds-ingest.log`):
  object-id phase found 9,396 ids; the writer opened 5,796 new versions,
  confirmed 3,600 as unchanged, superseded 0, rejected 0, left 0 undated — a
  first-ever load, not an idempotent no-op re-run. No re-ingest has happened
  since.
- **Revalidation is explicitly off** — the owner named `watersheds` (alongside
  `soil-survey`) as one of the layers that "stop revalidating," a static
  caching class rather than daily refresh (`conductor/RUNBOOK.md:2943-2944`,
  §0.21.5). **Settled — do not re-litigate.**
- `validation/models.py:143-146` (referenced by the RUNBOOK as
  `validate-streams` input) declares `watersheds` `kind="reference"` with
  **no `publication_cadence_days`** — so the existing stream-validation gate
  applies **zero** staleness check to this layer today
  (`conductor/RUNBOOK.md:876-882`).

## 3. Historical horizon

**Boundaries are a snapshot, not a series** — this is the load-bearing design
statement of the whole adapter, stated in its own module docstring:
*"one row per HUC12, refreshed in place... a re-run must land on the same row
rather than minting a new version of an unchanged polygon"*
(`watersheds.py:10-14`). `entity_local_id` and `producer_local_id` are both
the bare HUC12 code, with no timestamp folded in
(`build_watershed_identity`, `watersheds.py:114-129`).

- **The only date this product carries per feature is WBD's own `loaddate`**
  — the day USGS loaded or last touched that specific basin's boundary in the
  WBD, read as **epoch milliseconds** (`parse_load_date`,
  `watersheds.py:96-111`; a seconds-vs-milliseconds bug would land every
  basin in 1970, and a test locks this down —
  `tests/test_ingest_watersheds.py: test_the_load_date_is_read_as_epoch_milliseconds`).
  Measured example: the Sandy River HUC12s carry `loaddate = 1358492970000`,
  i.e. **2013-01-18**. This is emitted as `observedAt`
  (`build_watershed_write`, `watersheds.py:143-149`) because the read model
  dates every row from `COALESCE(observedAt, updatedAt, polygonDateTime)`, and
  `loaddate` is in none of those column names.
- **"History" here means each basin's own USGS-loaded vintage, not a
  resampled measurement.** A basin whose WBD `loaddate` is 2013 correctly
  draws at every slider date from 2013 onward; a basin the WBD adds later
  correctly does not appear before it existed
  (`drizzle/0017_watershed_persistence.sql:19-26`). A rollup row's
  `observed_day` is `max(observed_day)` across its member basins
  (`drizzle/0023_watershed_zoom_generalization.sql:52,64,75,86`) — itself a
  derived quantity, one more reason a coarse-zoom tile's date should never be
  read as a fresh measurement.
- **No archive of prior WBD revisions is fetched.** Only the current live WBD
  state is queried; the CLI docstring is explicit that there is no series to
  walk (`commands.py:270-272`).
- **The writer's general Type-2 geometry-version machinery still applies to
  this lane**, even though it has never been exercised here. `bind_feature_writer`
  logs `geometry_versions_maintained` with `confirmed`/`opened`/`superseded`/
  `undatable` counts whenever any of those actions occur
  (`services/agri-data-service/src/agri_data_service/ingest/writer.py:294-315`).
  The one real run opened 5,796 and confirmed 3,600 with **zero** superseded —
  consistent with a first-ever load, where nothing existed yet to supersede.
  **UNVERIFIED at the mechanism level** (not traced function-by-function in
  this pass): whether a genuinely revised USGS boundary for an existing HUC12
  code — same entity, changed geometry — would open a new version and
  supersede the old one, preserving the prior shape, or would simply overwrite
  in place. The architecture-decisions record (`geometry_versions_maintained`
  itself, plus this repo's settled Type-2 geometry-dimension design) points
  toward "supersede, don't overwrite," but this lane has produced zero
  supersessions to confirm it in practice.
- **Forecast horizon is `horizon: none`** — see §7 for the full reasoning.

## 4. Grain

Two distinct grains exist, in two distinct storage objects. **Only the first
is USGS's own data; the second is entirely computed by this repo.**

**Base (detail) layer — `geo.features`, layer `watersheds`.** One row = one
nationally-keyed HUC12 basin polygon, identified by its 12-digit HUC code.
Properties written (`build_watershed_write`, `watersheds.py:151-168`):
`huc12`, `name`, `areasqkm`, `tohuc` (the downstream basin's HUC), `states`,
`hutype`, `source` (`"USGS NHDPlus HR WBDHU12"`), plus `observedAt` when a
`loaddate` was parseable. Field names are the layer's own **lowercase**
GeoJSON spellings, not the title-case aliases the ArcGIS catalog displays —
`src/lib/map/hover-fields.ts` reads these exact keys
(`watersheds.py:154-164`). **9,396 rows** for the current PNW ingest bbox
(`-125,42,-111,49`), all from the single 2026-08-07 load (§2).

**Rollup — `geo.watershed_rollup`, a materialized view, not `geo.features`.**
One row = one **dissolved union** of every HUC12 basin sharing a common code
prefix, at one of four coarser rungs: HUC10, HUC8, HUC6, HUC4
(`drizzle/0023_watershed_zoom_generalization.sql:21-93`). Built
**hierarchically** — 12→10→8→6→4, each stage unioning ~10 neighbours rather
than re-unioning all 9,396 base polygons at every level — with the
simplification tolerance widening by level (0.0015/0.005/0.015/0.04,
`0023:50,61,72,83`). Columns: `huc_level`, `huc` (the prefix at that level),
`geom`, `areasqkm` (summed), `basin_count` (cumulative member count),
`observed_day` (`max()` of members). **`name`/`tohuc`/`hutype`/`states` are
NULL on every rollup row** — deliberate: *"a rolled-up basin has no single one
of any of them, and a member's value would be a lie about the whole"*
(`0023:126-130`). **2,162 rows total across the four rollup levels combined,
13 MB, carrying geometry** — matches both the task brief and the RUNBOOK's own
measurement (`conductor/RUNBOOK.md:954`, `:3995`). A code that is not exactly
12 digits takes no part in the rollup (`0023:42-44`, regex-guarded) — it stays
present at the base tier but forms no rollup group.

**Which grain the map actually draws depends on zoom**, chosen by
`geo.watershed_tiles(z,x,y)`: z≥10 reads the base layer directly (published
HUC12 detail); z<10 reads the rollup at the level the zoom can legibly carry
(HUC10 at z8-9, HUC8 at z6-7, HUC6 at z4-5, HUC4 below z4) — `0023:131-210`.
This exists because 0017's first tile function read `geo.features` at every
zoom, which meant a single request at low zoom had to transform, clip and
encode all 9,396 basins at once (`0017`'s successor migration header,
`0023:1-20`).

**Which grain a Parquet lane should carry — the base, the rollup, or both —
is addressed as a recommendation in §7**, since it is explicitly an open
design question the task calls out and not something settled in the repo
today.

## 5. Known gaps and traps

1. **The producer is complete and untriggered.** `ingest/watersheds.py` is a
   working, tested adapter with a registered CLI verb, and **nothing calls
   it** — no cron, no lane, not even `ingest-all` (§2). Anyone porting a
   "forward refresh" cron for this lane (required by
   `docs/layer-lane-standard.md` §13's checklist and implicitly by
   `conductor/code_styleguides/layer-lanes.md`) is **building a new
   capability**, not porting an existing one — there is nothing to port from.

2. **Two independent live paths serve "watersheds," and only one of them is
   what this migration means by the layer.** The **map** draws from
   `geo.watershed_tiles()` via Martin, reading the persisted `geo.features` /
   `geo.watershed_rollup` pair described in §4 — this is the path the Parquet
   migration replaces. The **`environmental.getWatersheds` tRPC procedure**
   (`src/lib/server/trpc/routers/environmental.ts:310-340`) is a **separate,
   still-live, un-warehoused proxy** straight to the same NHDPlus_HR endpoint
   via `hydrosheds.ts`, Redis-cached for one hour, capped at **1 square
   degree**. It feeds only `WaterDetails.tsx`'s Watersheds tab basin **list**
   (`WaterDetails.tsx:126,147,155-158`; `useWatershedsQuery`,
   `src/hooks/useViewportProxiedLayers.ts`), never the map layer itself
   (`src/lib/map/layer-registry.ts:246-262` states this explicitly: *"drawn
   from `geo.watershed_tiles()` rather than by proxying
   `environmental.getWatersheds` per viewport"*). At the app's default
   viewport (~767 square degrees per `conductor/RUNBOOK.md`) this proxy path
   **always** exceeds its own 1-square-degree cap, so the basin list is empty
   by design at ordinary zoom while the map still draws — `WaterDetails.tsx`
   explains the ceiling rather than reporting a fault
   (`WaterDetails.tsx:528-539`). **Migrating the tile-serving path to
   Parquet/PMTiles does nothing for this panel** unless it is separately
   repointed; flag as an open item for whoever owns wave-2 UI repointing, not
   something this lane's Parquet writer can fix by itself.

3. **`geo.refresh_watershed_rollup()` (the per-view convenience function from
   `0023`) is dead code — the RUNBOOK's framing of the rollup as simply
   "stale" needs a correction, recorded here.** Nothing in the repo calls that
   SQL function. The rollup's **actual** refresh path is the generic
   matview-refresh lane: `jobs/matview_refresh.py` registers
   `geo.watershed_rollup` in `MATVIEW_REFRESH_SPECS`
   (`matview_refresh.py:433-441`, `min_interval_seconds=86_400`,
   `max_staleness_seconds=604_800`), gated on a watermark scoped to the
   `watersheds` layer alone
   (`sql/jobs/matview_refresh_watermark_watershed_features.sql`), and issues a
   generic `REFRESH MATERIALIZED VIEW [CONCURRENTLY] <name>`
   (`matview_refresh.py:676`) — never the named function. Because nothing
   re-invokes `ingest-watersheds` (trap 1), that watermark has not moved since
   2026-08-07, so the refresh lane correctly has nothing new to do — it is
   **gated, not broken** (`conductor/RUNBOOK.md:954` calls this out the same
   way: *"gated, not failing"*). The RUNBOOK's separate line describing the
   rollup as *"serving stale data since it was built"*
   (`conductor/RUNBOOK.md:3995-3996`) is really a statement about the frozen
   base ingest, not a defect in the refresh mechanism itself — worth not
   repeating uncritically.

4. **Coastal geometry is not valid as published and needs an explicit repair
   sequence before any union.** Raw `ST_Union` over unrepaired WBD polygons
   fails with *"unable to assign free hole to a shell"* near `-124.73,
   48.38`. The fix, already implemented for the Postgres rollup: `ST_SnapToGrid`
   at a ~0.1 m grid, then `ST_MakeValid`, then `ST_CollectionExtract(..., 3)`
   to discard stray linework a repair can produce
   (`0023:26-30`). **Any DuckDB/Polars reimplementation of the rollup step
   must reproduce this exact repair sequence** or it will fail on the same
   coastal basins that already required it once.

5. **`data_available_at` is 100% NULL across all 9,396 watersheds rows**
   (full-layer scan, `conductor/RUNBOOK.md:905`) — not unique to this layer
   (five others share it), but worth carrying into any Parquet-schema
   discussion as a column whose current values are uniformly empty rather than
   silently omitted from the source data.

6. **The time-slider date-filter allowlist omits `watersheds`, even though
   `watershed_tiles` emits `observed_day` exactly like its siblings.**
   `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS`
   (`tile-layer-date-filter.ts`) and `DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE`
   (`LayerManager.tsx`) both list only `[fire-perimeters, evacuation-zones,
   burn-severity, sensors]` (`conductor/RUNBOOK.md:2793-2802`). For this layer
   specifically the effect is **over**-inclusion — boundaries draw at every
   date rather than empty-rendering — so it is benign today, not an outage.
   But it means the drift self-check
   (`reportDateFilterableToggleDrift()`) compares two copies of the same
   client list against each other, never against which SQL functions actually
   emit the attribute, so it cannot see this layer's true behavior either way.
   Worth not inheriting into a Parquet-era slider rewrite.

7. **Geometry is stored twice in today's Postgres row, a shape any Parquet
   schema decision should be aware of (without this document specifying
   one).** `build_watershed_write` writes the parsed GeoJSON `geometry` object
   into `properties` **in addition to** the native `geom` column
   (`watersheds.py:165`). Measured: one watersheds row carries `geom` **21,572
   bytes** of WKB and `properties->'geometry'` **56,780 bytes** of GeoJSON,
   inside one 16,237-byte compressed row (`conductor/RUNBOOK.md:1783-1785`).

8. **`water-gauges` is not structurally coupled to `watersheds` in the
   ingest layer** — checked directly, since the two are related upstream
   publishers (both USGS) and a sibling agent is documenting `water-gauges`
   separately. `services/agri-data-service/src/agri_data_service/ingest/usgs_nwis.py`
   (the streamflow/`water-gauges` adapter) contains **no** `huc`/`watershed`
   reference of any kind. The two lanes share only their upstream publisher
   family and geographic extent; **no gauge is ever assigned to, or joined
   against, a HUC12** anywhere in this codebase today. Do not assume a join
   key exists between the two lanes without adding one.

9. **A Redis pub/sub warning appears in the one real production run**:
   `realtime_publish_unavailable channel=layer:watersheds error=` (empty error
   string), logged at 2026-08-07 12:11:24
   (`.agri-local-runs/logs/watersheds-ingest.log`). Not further diagnosed in
   this pass. **UNVERIFIED** whether this recurs against production Redis or
   was a one-off local artifact — the run still completed and wrote all rows
   despite it.

## 6. Validation approach

Per `conductor/code_styleguides/layer-lanes.md` §4,
`pipeline/validation/watersheds.py` must reconcile what was **written**
against what the **source system** holds, never against local state alone.

- **A full reconciliation is realistic here in a way it is not for the
  high-volume lanes**, because this is a closed, ~9,400-polygon national
  boundary set, not an unbounded stream. The id-only query
  (`fetch_watershed_object_ids`, `watersheds.py:227-237`) already returns
  every OBJECTID the source holds for an extent in a few seconds, carrying no
  geometry — this is precisely the cheap "how many should exist" half of a
  validation pass, already precedented at PNW scale.
- **A shortfall is already detected, just only within one run, not
  continuously.** `fetch_watersheds` logs `wbdhu12_batch_shortfall` whenever a
  geometry batch returns fewer features than its own id list promised
  (`watersheds.py:259-263`). This exact check is the model for what a
  standalone validator should assert on a recurring basis against the
  **persisted/exported** HUC12 set, not just within a single ingest call.
- **The ArcGIS HTTP-200-with-error-object fault mode must be refused, not
  read as "zero basins."** Already implemented in `_query_json`
  (`watersheds.py:209-224`, raises `UpstreamPayloadError`) and locked down by
  a test (`tests/test_ingest_watersheds.py:
  test_an_arcgis_fault_behind_http_200_fails_the_job_rather_than_reporting_no_basins`).
  A validator reusing this endpoint must preserve the same refusal — an empty
  result must never be trusted without first checking for this fault shape.
- **The rollup's correctness is checkable by construction, without
  re-querying USGS at all.** Because a HUC-code prefix relationship is exact
  (not approximate), `sum(areasqkm)` of every HUC10 row sharing a given HUC8
  prefix must equal that HUC8 row's own `areasqkm` (and so on up through HUC6,
  HUC4). This is a strong, free regression assertion for any DuckDB/Polars
  reimplementation of the union step described in §4 — a mismatch is
  unambiguous, not a rounding question.
- **What "gap" means for this lane is a set-difference, not a day-axis
  check** — unlike a daily-pull lane, there is no calendar cadence to fail
  against (§2). The honest gap here is: the id-only query now returns a
  HUC12 not present in the persisted/exported set (WBD added or renumbered a
  basin), or the reverse (WBD retired one that is still persisted). Per
  `conductor/code_styleguides/layer-lanes.md` §4, an unfillable/unconfirmed
  day — or here, basin — should be recorded as a governed absence, never
  silently interpolated.
- **Nothing runs any of this automatically today.** There is no scheduled
  reconciliation job for `watersheds`, matching the same gap already recorded
  for `soil-survey` (`docs/lanes/soil-survey.md` §6). This is a wave-2 build
  item, not a port of an existing check.

## 7. Forecast recommendation: `horizon: none`

**State `horizon: none`. Ship no `method/monte_carlo/watersheds.py`.** Per
`conductor/code_styleguides/layer-lanes.md` §2: *"A lane that genuinely cannot
forecast declares `horizon: none` and ships **no**
`method/monte_carlo/<slug>.py`. An empty forecast module is worse than an
absent one: it reads as unfinished work rather than a settled property."*
This matches the already-settled owner classification in
`conductor/RUNBOOK.md:3347` (*"static; `horizon: none`"*) — recorded here, not
reopened.

**Reason, stated explicitly**: a HUC12 boundary carries no periodically
resampled quantity to project forward from. What changes over time is not a
measurement re-sampled on a cadence (like NDVI or a weather signal) but
USGS's own irregular, multi-year republication of line-work for a given basin
— a discrete, source-driven event on roughly an annual-or-slower schedule
(§2), not a continuous process with a trajectory. There is no 30-day-ahead
quantity for a watershed outline in the way there is for streamflow or fire
risk.

**Consequence for the `kind=observed`/`kind=forecast` sibling design**
(`conductor/code_styleguides/layer-lanes.md` §2): for this lane the sibling
collapses to one stream. Only `kind=observed` ever exists; there is no
forecast partition to delete on arrival, because none is ever written.

**What a day partition even means here, addressed directly, as the task
requires.** §0.23.6 already assumes static layers get one file per layer, no
day striation, and **names this lane explicitly**: *"Static layers get one
file per layer, no day striation — SSURGO, watersheds and anything without a
daily pull"* (`conductor/RUNBOOK.md:3243-3244`). Going further than that
assumption: this lane does not merely lack a *daily* pull, it currently lacks
**any** meaningful day axis to partition by at all. Two separate reasons,
worth keeping distinct:

1. The base layer is a **snapshot re-keyed in place per basin** (§3), not a
   daily-resampled measurement — there is no "today's reading" concept for a
   boundary the way there is for a sensor value.
2. Even the closest thing this product carries to a "day" — the WBD
   `loaddate` per basin — is a **per-feature vintage stamp**, and the measured
   production reality is that the **entire** persisted set shares a single
   load day: *"13 days static, exactly ONE load day — all 9,396 rows on
   08-07"* (`conductor/RUNBOOK.md:872`). Unlike `soil-survey`, whose vintage
   genuinely varies per survey-area and motivates keeping a vintage column
   (`docs/lanes/soil-survey.md` §3), watersheds' measured vintage spread today
   is exactly one date across the whole set. Partitioning the base layer by
   day, with today's data, would produce exactly one non-empty partition
   regardless of the layout chosen — itself evidence for treating this lane as
   day-agnostic rather than day-partitioned. **UNVERIFIED**: whether a future
   WBD republish would land basin-by-basin over time (reintroducing a real
   vintage spread, as SSURGO has) rather than all at once — nothing in the
   repo tests or documents that scenario, because it has never happened here.

**Which grain the Parquet lane itself should carry — the base boundaries, the
rollup, or both — is the other open question the task calls out. Recommendation,
not a settled decision**: the lane's own Parquet write should carry the
**base HUC12 boundaries**, because that is the only grain USGS itself
publishes — one row per basin, exactly what the §6 validation pass can
reconcile against the id-only query. The **rollup is not upstream data at
all**; it is a cartographic aggregate this repo computes (`ST_Union` +
simplify, `0023:21-93`) purely so low/mid-zoom tiles stay legible. Under the
stream plan's own division of labor, that already matches how a rollup is
supposed to be treated: `conductor/RUNBOOK.md:3323` assigns **S18 "Serving
aggregates — the rollups each serving surface actually reads"** to depend on
**S5-S15 (the eleven lane streams)** rather than being one of them — the same
relationship the rollup already has to the base layer in Postgres today (a
downstream materialized view, not a second ingest). Recomputing the rollup
from Parquet-persisted HUC12 boundaries — reproducing the coastal-geometry
repair from §5 trap 4 — reads as a serving-time/PMTiles-build-time concern
(adjacent to **S19, "PMTiles generation + Martin repoint"**), not something
this lane needs to write to object storage a second time. This is a
recommendation grounded in the existing architecture's own division of
concern, offered for whoever plans S18/S19 to confirm or override — it is not
asserted here as settled.
