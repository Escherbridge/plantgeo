---
type: lane-contract
slug: soil-survey
horizon: none
---

# soil-survey lane

Source-of-truth spec for the `soil-survey` layer lane, one of the eleven layers
named in `conductor/RUNBOOK.md` §0.24.2 (`conductor/RUNBOOK.md:3336-3348`) and
governed by `conductor/code_styleguides/layer-lanes.md`. This document does not
assert any Parquet path layout, filename, or column list — that contract is
being written concurrently by another agent. Where the repo does not establish
a fact, it is marked `UNVERIFIED` with what would confirm it.

## 1. Source system

- **Publisher**: USDA NRCS — SSURGO (Soil Survey Geographic Database), soil
  map-unit delineations.
- **Endpoint**: the USDA Soil Data Access (SDA) *tabular* SQL endpoint,
  `https://SDMDataAccess.nrcs.usda.gov/Tabular/post.rest`
  (`src/lib/server/services/usda-soil.ts:16-17`). Not the spatial WFS: probed
  live 2026-08-04, `Spatial/SDM.wfs` doesn't exist and
  `Spatial/SDMWGS84Geographic.wfs` has no JSON output and no attribute columns
  (`muname`, `compname`, `drainagecl`, `hydricrating`) at all — those live only
  in the tabular database (`src/lib/server/AGENTS.md:241-254`).
- **Query mechanism**: raw T-SQL against `mupolygon`/`mapunit`/`legend`/
  `sacatalog`/`component`, POSTed as JSON, scoped to one 1/8-degree-cell AOI per
  call (`usda-soil.ts:607-631`). Reads `mupolygon` directly rather than SDA's
  own clipping macro, because the macro returns viewport-clipped geometry keyed
  on the non-unique `mukey` — measured over one Boise cell, 683 delineations
  collapsed onto only 98 distinct `mukey`s (`src/lib/server/AGENTS.md:367-369`).
- **Auth**: none found anywhere in this module or in `docs/env-vars.md` — no API
  key, no credential header. The module self-limits to 3 concurrent requests
  (`usda-soil.ts:229`) as upstream courtesy, not because SDA requires it.
  UNVERIFIED: whether SDA enforces an undocumented rate limit beyond what's been
  measured.
- **Licensing**: **UNVERIFIED in this repo.** No `license_name`/`attribution`
  field is carried for SSURGO anywhere — contrast `geo.raster_release`, which
  makes `license_name`/`attribution`/`source_url` mandatory NOT NULL columns for
  SoilGrids (`drizzle/0024_soil_raster_release.sql:36-39`). SSURGO is a US
  federal government dataset and is conventionally public domain, but that is
  general knowledge, not something this codebase states or enforces. Confirm
  against USDA's current terms of use before publishing any attribution string
  in the Parquet export.

## 2. Cadence

**Not a periodic pull.** SSURGO issues no daily or scheduled re-observation.
What changes, per delineation, is the survey area's own **vintage**
(`sacatalog.saverest`) — a per-survey-area publication timestamp, not a
time-series sample.

- **Ingest today is lazy read-through**, triggered by a viewport pan warming
  uncovered grid cells (`usda-soil.ts:332-398`, see `getSoilSurvey`), not a
  cron. A bulk driver exists (`backfillSoilSurvey`, wrapped by
  `scripts/backfill-soil-survey.mjs`) but the RUNBOOK states it is
  **unscheduled** — nothing runs it automatically
  (`conductor/RUNBOOK.md:1041`).
- **Revalidation is explicitly off.** The owner named `soil-survey` (alongside
  `watersheds`) as one of the layers that "stop revalidating" — a static
  caching class rather than daily refresh (`conductor/RUNBOOK.md:2943-2944`,
  §0.21.5). **This decision is settled — do not re-litigate it.**
- **Vintage staggering, measured**: across the 220 SSURGO survey areas
  intersecting the PNW envelope, `saverest` spans **2025-08-26 to 2026-03-19**
  (`src/lib/server/AGENTS.md:378-379`). Different survey areas republish
  independently, on their own schedule — there is no single cadence the whole
  layer shares.
- **UNVERIFIED**: USDA's own publication interval per survey area (annual? on
  correction only?) is not stated anywhere in this repo. What *is* verified is
  the mechanism for detecting a republish: a strictly-newer `saverest` closes
  the prior Type-2 version and opens a new one (`usda-soil.ts:837-853`,
  `src/lib/server/AGENTS.md:387-393`), plus the one measured vintage-spread
  snapshot above.

## 3. Historical horizon

"History" does not mean a daily time series here. At any moment there is
exactly one live vintage per delineation (`geo.geometry.version_valid_to IS
NULL`); prior vintages are kept as closed Type-2 rows, not a calendar archive
(`usda-soil.ts:767-772`, `837-853`).

- The only axis a slider could offer is **the span of vintages actually
  stored**, keyed by survey-area publication, not wall-clock days of
  independent measurement.
- RUNBOOK §0.24.2 classifies this lane `horizon: none` for the forecast sense
  (`conductor/RUNBOOK.md:3346`); §0.21.5 separately exempts it from the
  daily-revalidation model entirely (`conductor/RUNBOOK.md:2943-2944`). Both
  are settled owner decisions, recorded here, not reopened.
- This is a sharper case than `docs/layer-lane-standard.md` §10's
  "current-only" declaration (a layer whose upstream is genuinely current-only
  should say so rather than fake an axis it can't fill,
  `docs/layer-lane-standard.md:250-255`): soil-survey isn't current-only either
  — it's **vintage-only**, and the vintage boundary is per-survey-area, not per
  day.

## 4. Grain

**One row = one persisted SSURGO delineation**, keyed on `mupolygon.mupolygonkey`
(SSURGO's own per-shape primary key), stored as one `geo.geometry` +
`geo.features` pair with `natural_key = 'usda-sda:<mupolygonkey>'`
(`usda-soil.ts:69`; producer token `usda-sda`).

Explicitly **not** `mukey` (map-unit key) — see the 683-into-98 collapse
measurement above. `mupolygonkey` is the only key that survives one delineation
mapping to multiple components/mukeys.

Two further shapes exist, both **computed per request from the same rows, never
separately persisted**:

- **Aggregated** — delineations unioned by `drainageClass` inside a bounded
  viewport (`usda-soil.ts:1061-1127`). Carries `mapUnitCount` and
  `hydricFraction`, never a `mukey` — structurally uncaptionable as one
  surveyed unit.
- **Summary** — delineations *counted* (not unioned) into a lattice cell, one
  point feature per non-empty cell, for viewports too wide to union honestly
  (`usda-soil.ts:1129` onward).

A third, distinct grain is the **coverage ledger**
(`geo.soil_survey_coverage`, `drizzle/0013_soil_survey_persistence.sql`): one
row per 1/8-degree grid cell (`SOIL_SURVEY_CELL_DEGREES = 0.125`,
`usda-soil.ts:59`), recording *whether* a cell has ever been fetched, not what
it contains. A ledger row with `polygon_count = 0` is authoritative
("unsurveyed here"); **no row at all means "never asked"** — the two are not
interchangeable (`drizzle/0013` header comment; `usda-soil.ts:146-153`).

Two now-dormant materialized views were built to precompute the aggregated and
summary shapes and are unused — see §5.

## 5. Known gaps and traps

1. **Persisted coverage is partial by construction, not backfilled.** Ingest is
   lazy read-through with an unscheduled bulk backfill (§2), so the current
   warehouse content depends on where users or the backfill script have already
   gone. Do not assume the store holds every SSURGO delineation for an area of
   interest unless the coverage ledger says so (`usda-soil.ts:340-347`).

2. **Two pre-aggregation matviews already exist for exactly the aggregation
   problem a Parquet lane will re-solve — read them before repeating the work:**
   - `geo.mv_soil_survey_union` **never once produced a row in production**
     until `drizzle/0035_soil_survey_union_collection_extract.sql` fixed a
     missing `ST_CollectionExtract` step in its `delineation` CTE (four
     consecutive failed refreshes recorded in `agri.matview_refresh_state`,
     `drizzle/0035_soil_survey_union_collection_extract.sql:1-4`). **This
     migration is DORMANT — unregistered in `drizzle/meta/_journal.json`, not
     applied to production** (`drizzle/0035...sql:61-63`). Recorded here so it
     is not re-diagnosed from scratch; **do not act on it.**
   - `geo.mv_soil_survey_grid`, by contrast, **does refresh and populate
     correctly today** (`drizzle/0035...sql:4`) — the difference is real, not
     a typo.
   - Even where populated, **neither matview has a reader.**
     `docs/pending-migrations/0029-pre-aggregation.md:93` and `:106` both say
     "not yet consumed by any reader." The live code explains why in-line:
     `mv_soil_survey_union`'s grain (`zoom_tier, drainage_class` — one global
     dissolve) doesn't match a viewport-scoped read, and repointing would trade
     a bounded `ST_Union` (≤20,000 rows) for an `ST_Intersection` against a
     continent-sized multipolygon — "worse on exactly the axis this work is
     about" (`usda-soil.ts:1049-1059`). `mv_soil_survey_grid`'s fixed 3-tier
     zoom ladder can't reproduce the live code's unbounded doubling ladder
     (`usda-soil.ts:1151-1158`).
   - Both are still refreshed on a schedule by the `matview-refresh` jobs-pulse
     lane despite having zero consumers
     (`services/agri-data-service/src/agri_data_service/jobs/matview_refresh.py:403,419`)
     — compute spent maintaining relations nothing reads.

3. **Ledger `polygon_count` does not sum to the true feature count.** A
   delineation straddling a cell boundary is fetched and counted by both
   cells but stored once — measured: 5 cells, ledger sum 2,675, distinct
   features 2,525 (`src/lib/server/AGENTS.md:448-451`). Never treat a naive sum
   of ledger rows as a completeness or row-count check.

4. **Per-cell truncation is real.** `MAX_SOIL_INGEST_POLYGONS_PER_CELL = 4000`
   caps one cell's stored delineations (`usda-soil.ts:40`); a cell that hits it
   is marked `truncated = true`. Density varies roughly an order of magnitude
   across CONUS — Corn Belt farmland measured at 2.6x the polygon density of
   arid Boise rangeland at the identical viewport area
   (`src/lib/server/AGENTS.md:269-278`) — so the ceiling is not uniformly safe
   headroom nationwide.

5. **`saverest` (vintage) has no timezone in the upstream payload**
   (`"8/27/2025 8:27:08 PM"`, US-locale text). The module deliberately discards
   the clock time and keeps the date at UTC midnight rather than assert a
   timezone the publisher never stated (`usda-soil.ts:735-761`). Any Parquet
   export carrying vintage forward must preserve this same deliberate
   imprecision, not reintroduce a fabricated instant.

6. **A dropped/unparseable row is a gap, counted, never silently discarded** —
   `unreadable_count` on the ledger, `unreadableGeometries` on the response
   (`src/lib/server/AGENTS.md:307-321`). The same "honest gap" discipline
   `docs/layer-lane-standard.md` §0 and §7 require elsewhere.

7. **`geo.soil_survey_coverage` has no `updated_at` column — only
   `fetched_at`.** An earlier design (the dormant 0029 pre-aggregation work)
   assumed `updated_at` for its matview watermark and was wrong; the corrected
   query lives at
   `services/agri-data-service/src/agri_data_service/sql/jobs/matview_refresh_watermark_soil_survey_coverage.sql`.
   Relevant to anyone computing "what changed since the last export" off this
   ledger.

8. **Backfill cost is real and measured, not a "static layers are free" case.**
   The PNW envelope alone (`-125,42,-111,49`, 6,272 cells) holds 1,507,623
   delineations across 44,332 mukeys and 220 survey areas; ~91h single-stream /
   ~30h at concurrency 3 from outside Railway's network, ~3h at concurrency 3
   from inside it (`src/lib/server/AGENTS.md:435-440`). A first full Parquet
   backfill for this lane is an hours-long, network-location-sensitive job.

9. **Documentation trap, already flagged live in the RUNBOOK — don't repeat
   it**: `infra/cron-soilgrids` and the `soil` raster toggle are a completely
   different concern from this lane. See §"SoilGrids vs soil-survey" below.

### SoilGrids vs soil-survey — a scope boundary, not the same lane

- Six SoilGrids COGs (ISRIC raster products) were published to R2 and
  catalogued via `geo.raster_release` in `drizzle/0024_soil_raster_release.sql`;
  production holds **12** live rows as of 2026-08-10
  (`conductor/RUNBOOK.md:3997-3998`) — 12, not 6, because each property
  publishes twice, once as an archival COG and once as a tile archive, under
  one partial-unique-index key on `(collection, property, depth, statistic,
  archive_format)` (`drizzle/0024_soil_raster_release.sql:72,76-77`).
- `raster_release` has **no relationship to `geo.layers`** — no `layer_id`, no
  row named `soil-survey`. It's served as PMTiles from R2 by range request,
  explicitly bypassing Martin (`drizzle/0024_soil_raster_release.sql`, header
  comment).
- SoilGrids/`soil-field` is **not one of the eleven lane slugs** enumerated in
  RUNBOOK §0.24.2 (`conductor/RUNBOOK.md:3336-3348`: `weather-observations,
  sensors, water-gauges, vegetation, fire-detections, fire-perimeters,
  burn-severity, interventions, soil-survey, watersheds, evacuation-zones`).
- **Conclusion: SoilGrids COGs are a separate raster-serving concern, not part
  of this lane.** This lane's scope is SSURGO map-unit delineations only.
  **UNVERIFIED**: which stream (if any) in §0.24.1's S0–S21 table is meant to
  carry the SoilGrids raster catalog into the Parquet/DuckDB world — the
  stream table doesn't name it. Wave-2 implementers should raise this rather
  than assume it silently falls under `soil-survey`.
- The RUNBOOK already records this exact confusion as a live documentation
  trap: `infra/cron-soilgrids` warms `public.soil_grid_cache` (ISRIC point
  rasters for the soil-field popup), **"not the `soil-survey` layer"**
  (`conductor/RUNBOOK.md:1038-1041`).

## 6. Validation approach

Per `conductor/code_styleguides/layer-lanes.md` §4, `pipeline/validation/<slug>.py`
must reconcile what was **written** against what the **source system** holds —
never against the lane's own intermediate state.

- **A full-count reconciliation against SDA is cheap and already
  precedented**: the whole PNW envelope's true count (1,507,623 delineations /
  44,332 mukeys / 220 survey areas) was counted directly at SDA in 23 seconds
  (`src/lib/server/AGENTS.md:436`). A validation pass can periodically re-run
  an equivalent count-only SDA query per validated area and compare it against
  the persisted `geo.features` count for that area — comparing against
  `geo.features`, never a naive sum of `soil_survey_coverage.polygon_count`
  (trap 3 above).
- **Per-cell health** is already tracked and should be asserted, not
  recomputed: for any cell the ledger claims covered, the steady state is
  `truncated = false` and `unreadable_count = 0`; nonzero values are
  themselves the signal (`drizzle/0013_soil_survey_persistence.sql` CHECK
  constraints; `usda-soil.ts:763-778`).
- **Vintage staleness** is the SSURGO-specific analogue of gap detection:
  compare stored `version_valid_from` / `properties.surveyAreaVintage` per
  survey area against SDA's live `sacatalog.saverest` for that `areasymbol`. A
  live `saverest` strictly newer than what's stored means a survey area
  republished and hasn't been re-ingested — a real, actionable gap.
- **Cadence for validation itself is an open decision, not a repo fact.**
  Given §2's per-survey-area (not daily) cadence, a periodic re-check (e.g.
  weekly/monthly) is the natural fit rather than `docs/layer-lane-standard.md`
  §8's daily gap-fill cadence for daily-pull lanes — but nothing in the repo
  declares this today. **UNVERIFIED / open** for whoever builds
  `pipeline/validation/soil-survey.py`.
- **Nothing runs any of this automatically today.** `backfillSoilSurvey()` is a
  manual CLI driver only (`scripts/backfill-soil-survey.mjs`); there is no
  scheduled reconciliation job for this lane, unlike FIRMS/MTBS/USDM which do
  have cron-driven validation (`docs/layer-lane-standard.md` §8). This is a gap
  against the lane standard's own §13 "definition of done" checklist, not a
  Parquet-migration-specific finding.

## 7. Forecast recommendation: `horizon: none`

**State `horizon: none`. Ship no `method/monte_carlo/soil-survey.py`.** Per
`conductor/code_styleguides/layer-lanes.md` §2: *"A lane that genuinely cannot
forecast declares `horizon: none` and ships **no**
`method/monte_carlo/<slug>.py`. An empty forecast module is worse than an
absent one: it reads as unfinished work rather than a settled property."*

This matches two already-settled owner decisions, recorded here, not reopened:
RUNBOOK §0.24.2's classification of the lane as static, `horizon: none`
(`conductor/RUNBOOK.md:3346`), and §0.21.5's decision to exempt it from daily
revalidation alongside `watersheds` (`conductor/RUNBOOK.md:2943-2944`).

**Reason, stated explicitly**: SSURGO delineations carry no periodic observed
axis to project forward from. What changes over time is not a measured
quantity re-sampled on a cadence (like NDVI or a weather signal) but a survey
area's own irregular, staggered republication (§2's measured 2025-08-26 →
2026-03-19 vintage spread across 220 PNW survey areas). A 30-day Monte Carlo
forecast — the shape every forecasting lane in this repo produces
(`conductor/code_styleguides/layer-lanes.md` §2-3) — has nothing to project:
drainage class and hydric rating have no trajectory over the next 30 days, and
the only thing that *can* change is a survey area republishing, which is a
discrete, source-driven event with no forecastable schedule, not a continuous
process.

**Consequence for the `kind=observed`/`kind=forecast` sibling-stream design**
(`conductor/code_styleguides/layer-lanes.md` §2): for this lane the sibling
collapses to one stream. Only `kind=observed` ever exists; there is no
forecast partition to delete on arrival, because none is ever written.

**The day-partition question, addressed directly, without specifying a path
layout**: because this lane's real versioning axis is per-survey-area vintage,
not a daily pull, "one Parquet file per day" is the wrong shape — §0.23.6
already assumes as much: *"Static layers get one file per layer, no day
striation — SSURGO, watersheds and anything without a daily pull"*
(`conductor/RUNBOOK.md:3243-3244`). What that assumption leaves open, and what
this lane's implementer must resolve: whether the single per-layer file needs
vintage/survey-area encoded as a column so the Type-2 versioning this lane
already performs in Postgres (`version_valid_from`/`version_valid_to` per
delineation, `usda-soil.ts:767-772`) survives the move, or whether "static"
here is meant as "current snapshot only, history discarded." Given the lane's
whole persistence design exists specifically to keep vintages
(`drizzle/0013_soil_survey_persistence.sql` header: *"persisted per
survey-area vintage"*), collapsing to a snapshot-only file would be a real
regression, not a simplification. **Flagging this as the one open design
question wave-2 must answer explicitly, rather than default silently.**
