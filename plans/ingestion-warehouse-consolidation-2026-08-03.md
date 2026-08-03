# Ingestion & Warehouse Consolidation

**Date:** 2026-08-03 · **Revised:** 2026-08-03 (owner decisions D1-D4) · **Status:** plan, not yet started · **Scope:** move all ingestion to Python, persist every presented data source in our own layer, key everything to one conformed geometry dimension, and serve past→future through a single day-granular read path.

## Framing

> "I want to persist all data used and presented from our own data layer, even if it's an outside data source. I want a simple data warehouse structure and clean developer experience for a solo dev building this app and trying to balance scope and value."

> "the ML pipeline can likely derive from these same values and create the forecast/model + layer presented as a 5 day, 10 day and 30 day forecast — statistical Monte Carlo and ML version options on the front end."

Practical research tool, one solo dev, not multi-org. Ceremony is a liability.

### Owner decisions folded into this revision

| # | Decision | Owner's words | Supersedes |
|---|---|---|---|
| **D1** | **Geometry is a conformed dimension.** Star schema: one `geo.geometry` table, everything else a fact keyed to `geometry_id`. A grid cell is just a geometry row. | *"it's not about storage it's about maintainability and consistency. I would rather have geometry as a dimension and everything else as facts linked to geometry."* | the "which `spatial_cell` grid?" question (old open Q1) |
| **D2** | **The map forecast is a time slider, not a metric.** One day-granular slider, past → today → +30 d. Toggle picks statistical vs ML **for future days only**. | *"I want a slider from past to current to the future planes. People should be able to select and view each day, and it should just indicate that it switched to ML forecasts or statistical forecasts for future dates, set by a toggle for that slider."* | "5/10/30-day horizon buttons" (§6) and "which metric goes on the map?" (old open Q2) |
| **D3** | **MTBS burn severity is in scope.** Persist it; settle licensing later. | *"we need to persist it so lets do it, we can figure out licensing or whatever later."* | the "no MTBS" non-goal |
| **D4** | **Remove the evaluation-only lock.** Drop the two `forecast_iteration` CHECKs and the eval-only gating. | *"totally remove the eval only logic and check constraints, lets just keep things moving."* | "widen the CHECKs, never drop them" (old risk 2) |

Also already decided today, and load-bearing here:

- **The checksum *enforcement* layer is cut.** Keep the checksum columns and the 11 `*_checksum` functions; delete `finalize_*` (4 fns), the `guard_*` triggers, and convert the two `GENERATED ALWAYS … STORED` `value_checksum` columns to plain columns (`db/agri/tables/forecast_iteration_value.sql:17`, `forecast_hindcast_value.sql:25`). See `services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md` §6 Option 3.
- **The receipt / publication / hindcast planes are cut** — owner: *"does not seem needed lets cut it."* This has a consequence the audit did not measure; see risk 3.
- **`agri` is live in production at head `20260803_0017`: 69 tables, 93 routines, 0 rows.** Destructive simplification is free **right now**. That window closes the moment Phase 4 writes the first `source_release` row — which is why Phases 2 and 3 come before it.

**Ordering constraint that follows:** governance cut (Phase 2) and the geometry repoint (Phase 3) must both land before any phase writes an `agri` row.

### The single most important finding

**The warehouse already exists.** `agri` has a complete provenance spine and a generic observation plane, both shipped and tested:

| Object | Purpose | Defined at |
|---|---|---|
| `agri.data_source` | governed source identity + license + citation + retention | `services/agri-data-service/alembic/versions/20260719_0001_agri_foundation.py:362-397` |
| `agri.source_release` | one immutable fetch: `retrieved_at`, **`data_available_at`**, `payload_checksum`, `license_snapshot`, `validation_state` | `:400-441` |
| `agri.artifact` | the raw bytes: `uri`, `checksum_sha256`, `size_bytes`, `storage_class`, optional inline `content_bytes` | `:446-470` |
| `agri.release_set` / `release_set_item` | frozen input bundle for a reproducible model run | `:472-512` |
| `agri.spatial_cell` | grid cells — **0 rows, no grids defined** | `20260720_0002_historical_observation_plane.py:52-81`, shape at `db/agri/tables/spatial_cell.sql:7-20` |
| `agri.cell_source_crosswalk` | native→canonical mapping, already carrying `spatial_support_kind`, `mapping_method`, `coverage_fraction`, `native_geometry` — **currently unused** | `db/agri/tables/cell_source_crosswalk.sql:7-22` |
| `agri.signal_observation` | long-format scalar time series: `signal_name`, `observed_at`, **`data_available_at`**, `normalized_value`, `quality_flag`, `is_observed` | `:131-184`, shape at `db/agri/tables/signal_observation.sql:7-27` |
| `agri.drought_polygon_snapshot` | governed geometry snapshots with `issue_date` + `data_available_at` | `:285-320` |

**Do not design a new warehouse.** This plan generalizes what is there and adds exactly one new idea — the conformed geometry dimension (D1). Net-new schema across the whole plan: **three Alembic changes and two Drizzle tables.** Everything else is DDL *deletion*, DML, and CLI code.

---

## 1. Target architecture

```
                         ┌─────────────────────────────────────────┐
  upstream (11 sources)  │  plantgeo-ingest-cron   [Railway cron]   │
  ─────────────────────▶ │  python -m agri_data_service.cli ...     │
  FIRMS  NWIS  Open-Meteo│  runs to completion, exits 0 or non-zero │
  WFIGS  USDM  GIBS-NDVI │  0 * * * *   restartPolicyType: NEVER    │
  SoilGrids Terrain      └───────────┬──────────────────┬──────────┘
  NLCD  LANDFIRE  MTBS               │ DML              │ PUT
                                     ▼                  ▼
              ┌──────────────────────────────┐   ┌──────────────────┐
              │  plantgeo  (PG18 + PostGIS)  │   │ Cloudflare R2    │
              │                              │   │ tiles.aevani.com │
              │  geo    ← Drizzle owns DDL   │   │ *.pmtiles        │
              │    ┌──────────────────────┐  │   │ raw payloads     │
              │    │ geometry  ◀ THE ONE  │  │   │ (artifact.uri)   │
              │    │ CONFORMED DIMENSION  │  │   └──────────────────┘
              │    └─────▲──────────▲─────┘  │            ▲
              │          │          │        │            │
              │   layers/features   │  metric_daily (new) │
              │   drought_areas     │  ← NARROW FACT      │
              │                     │    observed+forecast│
              │  agri   ← Alembic owns DDL  │             │
              │    data_source              │             │
              │    source_release ─ artifact ─────────────┘
              │    cell_source_crosswalk    │
              │    signal_observation ← FEATURE STORE
              │    drought_polygon_snapshot │
              │    forecast_iteration(_value)│
              └───────┬──────────────────────┘
                      │ read-only
        ┌─────────────┴──────────────┐
        ▼                            ▼
   plantgeo-main (Next.js)      Martin  → MVT
   tRPC / API routes            → MapLibre
   ├ time slider (day-granular)
   └ variant toggle (MC | ML), future days only
```

**The arrows all point at `geo.geometry`.** That is the whole of D1: one place defines what a thing *is* and where it *is*; every other table says only *what was measured or predicted there, on what day*.

**What changes:**

| | Before | After |
|---|---|---|
| Scheduler | `curlimages/curl:8.10.1` (`infra/cron-ingest/Dockerfile`) curls `/api/cron/ingest` | Python image built from `services/agri-data-service/Dockerfile`, `CMD` runs the CLI |
| Execution | route returns `202 {status:"started"}` and runs jobs **detached** (`src/app/api/cron/ingest/route.ts:31-44`) because a full run outlives the ~100 s edge timeout (`:8-11`) | container runs to completion; process exit code is the result |
| Concurrency guard | module-level `let ingestionInFlight` (`route.ts:12`) — a single in-memory boolean on one replica | Railway cron + `restartPolicyType: NEVER` (`infra/cron-ingest/railway.json`) already means one run at a time; plus the existing per-run checkpoint files |
| Failure visibility | swallowed into `console.log`/`console.error` (`route.ts:33-41`); a failed run looks identical to a successful one | non-zero exit ⇒ red Railway run ⇒ notifiable |
| Auth | `CRON_SECRET` header over public HTTPS (`authorizeCronRequest`) | none needed — the container talks to the DB on the private network |

**Delete after cutover:** `src/app/api/cron/ingest/route.ts`, `runAllIngestionJobs` (`ingestion-jobs.ts:391-423`), `infra/cron-ingest/Dockerfile`'s curl base. Keep the six TS job functions read-only until Phase 1 verification passes, then delete them too.

**Explicitly:** the cron container runs to completion and exits non-zero on failure. That replaces the detached-promise pattern, which is the primary reliability motivation for this whole move — today a job can throw after the 202 is returned and nothing anywhere is red.

---

## 2. Warehouse structure

One conformed dimension, two storage classes, one provenance spine. That is the whole model.

### 2.0 The conformed geometry dimension (D1)

**Not a storage optimisation.** `geo.features` geometry totals **7.3 MB inside a 96 MB table** across ~15 k `ST_Point` rows; there are 22 geometry columns database-wide. Normalising geometry saves nothing worth counting. The motivation is maintainability and consistency, and it is concrete:

**Identity is defined in six places today.** Dedupe is `properties->>'id'` compared inside `ingestResolvedBatch` (`src/lib/server/services/ingest.ts:56-65`), and each of the six ingestion jobs builds that string its own way (`src/lib/server/services/ingestion-jobs.ts:100-115` FIRMS, `:189` gauges, `:294` weather, `:335` perimeters). The only DB-side guard is a partial unique index scoped to one layer — `features_layer_external_id_unique` on `(layer_id, properties->>'id')` (`src/lib/server/db/schema.ts:181-184`) — which prevents a *duplicate* key but does nothing about a *changed* one. That is the plan's own critical "feature-ID drift" risk, and it is a direct consequence of identity having six definitions.

**`geo.geometry.natural_key` is where identity gets defined once.** Every producer resolves a `natural_key` to a `geometry_id` through one function; nothing downstream ever re-derives an identity string.

```sql
-- Drizzle owns this. See "Migration ordering" below for why that matters.
CREATE TABLE geo.geometry (
  geometry_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  natural_key   varchar(255) NOT NULL,                    -- '<producer>:<producer-local id>'
  geom_kind     varchar(16)  NOT NULL,                    -- point|polygon|line|grid_cell
  geom          geometry(Geometry,4326) NOT NULL,
  centroid      geometry(Point,4326)    NOT NULL,         -- plain column, written at insert
  grid_name     varchar(100),                             -- grid cells only
  cell_key      varchar(180),                             -- grid cells only
  resolution_m  integer,                                  -- grid cells only
  producer      varchar(100) NOT NULL,                    -- which ingest minted it
  first_seen_at timestamptz NOT NULL DEFAULT now(),        -- set on INSERT only, never on UPDATE
  last_seen_at  timestamptz NOT NULL DEFAULT now(),        -- bumped on every resolve
  CONSTRAINT uq_geometry_natural_key UNIQUE (natural_key),
  CONSTRAINT ck_geometry_kind CHECK (geom_kind IN ('point','polygon','line','grid_cell')),
  CONSTRAINT ck_geometry_cell_fields CHECK (
    (geom_kind <> 'grid_cell' AND grid_name IS NULL AND cell_key IS NULL AND resolution_m IS NULL)
    OR (geom_kind = 'grid_cell' AND grid_name IS NOT NULL AND cell_key IS NOT NULL AND resolution_m > 0)),
  CONSTRAINT uq_geometry_grid_cell UNIQUE (grid_name, cell_key)
);
CREATE INDEX ix_geometry_geom     ON geo.geometry USING GIST (geom);
CREATE INDEX ix_geometry_centroid ON geo.geometry USING GIST (centroid);
CREATE INDEX ix_geometry_kind     ON geo.geometry (geom_kind, producer);
```

Four refinements to the sketch, each with a reason:

| Refinement | Why |
|---|---|
| **`natural_key` is namespaced** (`firms:<sat>:<acqDate>:<acqTime>:<lat4>:<lon4>`), not the bare producer-local id | Today's key is unique only *within a layer* (`schema.ts:181-184`). A globally-UNIQUE column over unnamespaced ids would silently merge a FIRMS detection and a gauge that happened to share a string. The namespace is not decoration; it is what makes the UNIQUE legal. |
| **`centroid` is a plain column, not `GENERATED … STORED`** | A stored generated column calling a PostGIS function is exactly the mechanism that made the warehouse unrestorable from its own `pg_dump` (`services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md:178-186`). We are deleting the other two this quarter; do not add a third. |
| **`resolution_m` and `producer` added** | `resolution_m` mirrors `agri.spatial_cell.resolution_m` (`db/agri/tables/spatial_cell.sql:11`) so retiring that table loses nothing. `producer` makes "which ingest owns this row" answerable without parsing `natural_key`. |
| **`first_seen_at` and `last_seen_at` are split, and `first_seen_at` is insert-only** | `geo.features.created_at` is measurably *not* a first-seen timestamp — the refresh-in-place path rewrites it (see "Backfill" below), so all 15 016 rows read as created today. The dimension must not repeat that: one column that only ever means "first", one that only ever means "most recent". |
| **No `valid_from`/`valid_to`** — the dimension is Type-1 | WFIGS redraws a perimeter under a stable `uniqueFireIdentifier` (`ingestion-jobs.ts:335`), so a redraw updates `geom` in place and bumps `last_seen_at`. **This is a real trade-off, not an oversight:** with D2's slider, scrubbing to last week will show *today's* perimeter shape. See open question 4. |

**Facts reference `geometry_id`. A grid cell is a geometry row with `geom_kind='grid_cell'` — grid cells stop being a separate concept.**

| Fact | Change |
|---|---|
| `geo.features` | gains `geometry_id uuid REFERENCES geo.geometry`; keeps its own `geom` denormalised for tile serving (see "Martin" below) |
| `agri.signal_observation.cell_id` | becomes `geometry_id`, cross-schema FK → `geo.geometry` |
| `agri.forecast_series.spatial_cell_id` | becomes `geometry_id` (`db/agri/tables/forecast_series.sql:22`) |
| `agri.cell_source_crosswalk.cell_id` | becomes `geometry_id` |
| `geo.metric_daily` (new, §2.1) | keyed on `geometry_id` from birth |

**`agri.spatial_cell` retires.** Verified: **0 rows and no grids defined**, so this costs nothing — drop it, or leave it as a view over `geo.geometry WHERE geom_kind='grid_cell'` if any SQL still names it. Prefer the view for one revision, then drop.

**`agri.cell_source_crosswalk` is reused, not reinvented.** It already models native→canonical mapping with `spatial_support_kind` ('native_grid_cell' | 'native_polygon' | 'point_sample' | 'area_aggregate' | 'unknown'), `mapping_method`, `coverage_fraction` and `native_geometry` (`db/agri/tables/cell_source_crosswalk.sql:7-22`). NDVI at 250 m, NLCD at 30 m and MTBS fire polygons resolve onto canonical cells through this table. **Do not write a new resampling table.** It is unused today only because nothing has needed it yet.

#### Migration ordering — new load-bearing coupling

`geo.geometry` is **Drizzle-owned**, deliberately: the map's serving path must never wait on a manual step. `preDeployCommand` runs Drizzle automatically on every Railway deploy; Alembic is run by hand. If the dimension were Alembic-owned, a fresh environment would deploy a map with no geometry table.

The consequence: with cross-schema FKs `agri.* → geo.geometry`, **on a fresh database Drizzle must migrate before Alembic.** Cross-schema FK precedent already exists in the other direction — `geo.layers.team_id → public.teams.id` (`src/lib/server/db/schema.ts:159`).

- The Alembic revision that adds the FK opens with `SELECT to_regclass('geo.geometry')` and raises a readable error if it is NULL, rather than failing on a bare FK violation.
- **This belongs in `services/agri-data-service/db/AGENTS.md`** as a short subsection after "Relationship to Alembic (read this first)" (`db/AGENTS.md:13`). *Not edited by this plan — add it in the Phase 3 commit.*
- Adding `geo.geometry` and `geo.metric_daily` are Drizzle migrations ⇒ `src/lib/server/db/migration-contract.ts` must be updated in the same commit (current head `0007_governed_environmental_ingestion`, `migration-contract.ts:2`).

#### Backfill

`geo.features` → `geo.geometry`, census and insert in **one transaction**, asserted on both sides:

```sql
-- all three steps in ONE transaction. The census is not a preflight you run
-- and then eyeball; it is the assertion's left-hand side.
BEGIN;

-- 1. census
CREATE TEMP TABLE backfill_census ON COMMIT DROP AS
SELECT count(*) FILTER (WHERE properties ? 'id' AND geom IS NOT NULL) AS eligible,
       count(*) FILTER (WHERE NOT (properties ? 'id'))                AS no_natural_key,
       count(*) FILTER (WHERE geom IS NULL)                           AS no_geometry,
       count(*)                                                       AS total
FROM geo.features;

-- 2. insert
INSERT INTO geo.geometry (natural_key, geom_kind, geom, centroid, producer)
SELECT l.name || ':' || (f.properties ->> 'id'),
       lower(replace(GeometryType(f.geom), 'MULTI', '')),
       f.geom, ST_Centroid(f.geom), l.name
FROM geo.features f JOIN geo.layers l ON l.id = f.layer_id
WHERE f.properties ? 'id' AND f.geom IS NOT NULL;

-- 3. assert, then COMMIT: eligible == inserted, and every eligible feature
--    resolved a geometry_id. Raise and ROLLBACK on mismatch.
COMMIT;
```

**Do not hardcode the expected count — including from this document.** Measured against production during this revision: the four ingested layers hold **15 016** rows (water-gauges 7 596, fire-detections 6 297, weather-observations 1 013, fire-perimeters 110). The earlier draft of this plan cited 12 287 from a capture taken the same morning; the ~2.7 k difference is entirely water-gauges (+2 244) and weather-observations (+485) written by ingestion runs in between. **Nothing was wrong with either number — the cron simply runs, so any figure written into a document is stale within hours.** That is the actual hazard: an assertion compared against a literal will fail spuriously (or, worse, pass for the wrong reason) whenever ingestion has run since the literal was written. Compare against a count taken inside the same transaction, always.

**Every row has a natural key.** Measured: `count(*) FILTER (WHERE NOT (properties ? 'id')) = 0` for all four layers, all 15 016 rows. The backfill has a usable `natural_key` for 100 % of current data, and the `no_natural_key` / `no_geometry` census columns above exist as tripwires, not as an expected shortfall. **The namespacing requirement is unaffected** — ids are unique per-layer, not globally (`features_layer_external_id_unique`, `src/lib/server/db/schema.ts:181-184`) — and that remains the critical risk.

**Forward-looking requirement instead:** `ingest/identity.py` must **reject** a feature whose upstream supplies no stable native key, rather than synthesising one from coordinates or a hash of the payload. A synthesised key looks identical to a real one in the dimension and silently mints a new geometry every time the upstream jitters a decimal place. Every new source in Phase 5 must name its native key field in its ingest module, and MTBS's (`Fire_ID`, `mtbs.ts:48`) is the model for that.

**`geo.features.created_at` is "last touched", not "first seen" — do not inherit it.** Measured: all 15 016 rows carry `created_at` = today, including fire-detections and fire-perimeters whose newest upstream data predates today's runs. The refresh-in-place path (`src/lib/server/services/ingest.ts:107-122`) rewrites the row on any payload change, so the column tracks the most recent write. Consequences:

- The backfill must **not** copy `created_at` into `geo.geometry.first_seen_at`; the dimension's `first_seen_at` starts honest at backfill time and is only ever set on insert, never on update.
- Nothing may derive `data_available_at` from `created_at` (§5b) — it would report "knowable today" for a three-day-old detection.
- The slider's history depth (§6) cannot be computed from `created_at` either; it comes from `geo.metric_daily.valid_on`, which is an observation date, not a write date.

### 2.1 The narrow fact table (D1 + D2 together)

D2 demands that **"value at (`geometry_id`, `valid_on`, `metric_name`)" has the same shape whether it came from an observation or a forecast** — the slider must not care which side of today it is on. That is a stronger argument for the narrow fact table than the star schema alone: if observed and forecast live in differently-shaped tables, every read path, every cache key and every legend has to branch on "is this date in the future?", and one of those branches will eventually be wrong.

So: **one table, one shape, `value_kind` as a column.**

```sql
CREATE TABLE geo.metric_daily (
  geometry_id   uuid         NOT NULL REFERENCES geo.geometry(geometry_id) ON DELETE CASCADE,
  metric_name   varchar(150) NOT NULL,
  valid_on      date         NOT NULL,
  issued_on     date         NOT NULL,      -- observations: = valid_on, so the PK stays NOT NULL
  variant       varchar(24)  NOT NULL,      -- 'observed' | 'monte_carlo' | 'ml'
  value_kind    varchar(16)  NOT NULL,      -- 'observed' | 'forecast'
  median_value  double precision NOT NULL,  -- the value; p50 for forecasts
  low_value     double precision,           -- NULL for observations; p10 for ML
  high_value    double precision,           -- NULL for observations; p90 for ML
  purpose       varchar(32)  NOT NULL DEFAULT 'serving',  -- see the D4 recommendation
  provenance_key varchar(255) NOT NULL,     -- source_release id, or forecast iteration_key
  PRIMARY KEY (geometry_id, metric_name, variant, valid_on, issued_on),
  CONSTRAINT ck_metric_daily_kind CHECK (
    (value_kind = 'observed' AND variant = 'observed'  AND issued_on = valid_on)
    OR (value_kind = 'forecast' AND variant IN ('monte_carlo','ml') AND issued_on <= valid_on)),
  CONSTRAINT ck_metric_daily_band CHECK (
    low_value IS NULL OR (low_value <= median_value AND median_value <= high_value)),
  CONSTRAINT ck_metric_daily_horizon CHECK (valid_on - issued_on BETWEEN 0 AND 30)
);
CREATE INDEX ix_metric_daily_read
  ON geo.metric_daily (metric_name, valid_on, variant, issued_on DESC);
```

This **replaces `geo.forecast_cell_daily`** from the original plan. That table carried its own `geom` and its own `cell_id` mirror of `agri.spatial_cell` with no FK — exactly the duplicated-identity pattern D1 exists to remove — and it could only ever hold forecasts, which forces the slider to branch at today.

**Where observed and forecast are unioned: in the batch projector, not on the read path.** Two CLI verbs write the same table — `observations-project-serving` (from `agri.signal_observation` and `geo.drought_areas`) and `forecast-project-serving` (from both forecast lanes, normalised to the same five columns). The read is then one indexed query with no `UNION ALL` and no branch. **That the union disappears from the read path is the point**, and it is only possible because the shape is uniform.

The unified read, as one tRPC procedure `environmental.getMetricAtDate({ metric, date, variant, bbox })`:

```sql
SELECT DISTINCT ON (m.geometry_id)
       m.geometry_id, g.geom, m.median_value, m.low_value, m.high_value,
       m.value_kind, m.variant, m.issued_on, m.provenance_key
FROM   geo.metric_daily m
JOIN   geo.geometry     g USING (geometry_id)
WHERE  m.metric_name = $metric
  AND  m.valid_on    = $date
  AND  m.variant     = CASE WHEN $date <= current_date THEN 'observed' ELSE $variant END
  AND  m.purpose     = 'serving'          -- D4 recommendation; delete this line if declined
  AND  m.issued_on  <= current_date
  AND  g.geom && ST_MakeEnvelope($w,$s,$e,$n,4326)
ORDER BY m.geometry_id, m.issued_on DESC; -- newest admissible issue wins
```

`DISTINCT ON … ORDER BY … DESC` is the same "latest admissible release wins" idiom the covariate layer already uses (`db/agri/functions/covariate_daily_features.sql`), so it is not a new pattern to learn.

**Retention.** Unpruned, a 30-day horizon re-issued nightly writes 30 rows per geometry per metric per variant per day. Keep every issue for the trailing 14 days (enough to answer "did the forecast move?"), then prune to the newest issue per `(geometry_id, metric_name, variant, valid_on)`. One `DELETE` at the end of the nightly job.

**Martin and the PK join — flagged honestly.** Every tile query gains a join. Today the tile functions read `FROM geo.features f JOIN geo.layers l ON f.layer_id = l.id` (`drizzle/0001_handy_riptide.sql:395-396`; same shape at `drizzle/0005_intervention_priority_tiles.sql:29-30`), so the added `JOIN geo.geometry` is a second indexed nested loop on a PK — cheap, but real. Three findings:

1. **Existing MVT layers do not need to change at all.** `geo.features.geom` stays denormalised; the dimension is the identity authority, not the render source. Zero tile-function edits in any phase of this plan.
2. **The slider layer cannot be MVT as configured.** All four declared function sources take `(z, x, y)` only (`infra/martin/martin.yaml:29-40`, definitions at `drizzle/0001_handy_riptide.sql:376,412,448,485`). There is no place to put a date. **Serve the slider layer as GeoJSON through the tRPC procedure above**, the way drought already works (`src/lib/server/services/environmental-read-model.ts:405-432`) — the row count is a few thousand cells, not a basemap.
3. **No materialised serving view yet.** `geo.metric_daily` *is* the projection; a matview over it is a projection of a projection. Add one only if the measured p95 on the read above exceeds ~300 ms at full-bbox zoom.

### Storage classes

| Class | What lives there | Why |
|---|---|---|
| **PostGIS** (`plantgeo`) | scalar time series, vector geometry, forecast values, all metadata | queryable, joinable, transactional; the model reads from here |
| **Cloudflare R2** (`tiles.aevani.com`) | raster PMTiles archives, raw upstream payloads over ~1 MB | rasters are not query targets; R2 is $0.015/GB-mo with zero egress and the CDN is already wired (`scripts/deploy-pmtiles.sh`, `src/lib/map/sources.ts:9-10`) |

Every R2 object is registered as an `agri.artifact` row (`uri`, `checksum_sha256`, `size_bytes`, `storage_class='r2'`) hanging off the `source_release` that produced it. **There is no separate raster catalog table** — `artifact` already is one.

### Provenance spine — two lanes, deliberately

What survives Phase 2's cut — release-set membership and the checksum *columns* — is still worth having for model inputs and still far too heavy for an hourly map refresh. Split it:

| Lane | Applies to | Writes | Does **not** write |
|---|---|---|---|
| **Operational** | every scheduled fetch that feeds a map layer | `geo.geometry` resolve/upsert, then `source_release` (+`artifact` if payload >1 MB) → DML into `geo.*` | `release_set`, checksummed manifests |
| **Governed** | any release that becomes a model input | everything above, **plus** normalized rows into `agri.signal_observation` and membership in a `release_set` | — |

`agri.signal_observation` has an FK to `source_release` only, not to `release_set` (`20260720_0002:134`), so the operational lane is legal without loosening anything. A source is promoted from operational to governed by adding one `release_set_item` row and a `covariate_feature_schema` entry — nothing is re-ingested.

### DDL sketches

**A. Remove the evaluation-only lock — Alembic (D4).** Today serving is illegal:

```sql
-- current, 20260723_0010_forecast_iteration_pipeline.py:1132-1136
-- (head shape: db/agri/tables/forecast_iteration.sql:44,48)
CONSTRAINT ck_forecast_iteration_method  CHECK (method = 'daily_increment_bootstrap_v1'),
CONSTRAINT ck_forecast_iteration_purpose CHECK (purpose = 'evaluation_only'),
```

```sql
-- D4: drop them outright, and do not replace them with narrower ones.
ALTER TABLE agri.forecast_iteration
  DROP CONSTRAINT ck_forecast_iteration_method,
  DROP CONSTRAINT ck_forecast_iteration_purpose,
  ALTER COLUMN purpose SET DEFAULT 'serving';
-- availability_mode stays IN ('as_of_pinned_release','retrospective_pinned_release');
-- serving runs use 'as_of_pinned_release'.
```

**Flip the default too.** `purpose` currently defaults to `'evaluation_only'` (`db/agri/tables/forecast_iteration.sql:11`). Drop the CHECK and leave the default alone and every serving run that forgets to pass `purpose` silently lands as an evaluation. Serving is now the common path; make it the default.

> **Recommendation (declinable, costs nothing).** Keep `purpose` as a plain **column** — no CHECK, no trigger, no ceremony — and have the *serving read path* filter on it: `WHERE purpose = 'serving'` in `forecast-project-serving` and in the `geo.metric_daily` read (§2.1). That single WHERE clause is what stops a backtest or hindcast from surfacing on the map as a live forecast. It is one line in two places, not a governance layer.
>
> **If you decline it:** delete the `purpose` column and the two WHERE clauses. The plan still works — `geo.metric_daily` is a projection, so a bad batch is fixed by re-running the projector, not by editing rows. The only thing lost is the guarantee that it never *reaches* the map in the first place.

**Also in this revision (the checksum-layer cut):** drop `finalize_*` (4 fns, 886 lines), the `guard_*` triggers, the 10 status↔checksum evidence CHECKs, and convert the two `GENERATED ALWAYS … STORED` `value_checksum` columns to plain columns. Keep every checksum column, the 11 `*_checksum` functions, the identity UNIQUEs, and `materialize_forecast_iteration`'s idempotency block. Full inventory and rationale: `services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md` §6.

**B. Covariate schema `agri_covariates_v2` — Alembic, function bodies only.** No table changes. Adds stream keys for the newly in-housed sources (§5).

**C. `geo.geometry` and `geo.metric_daily` — Drizzle.** Defined in §2.0 and §2.1. `geo.forecast_cell_daily` from the previous revision of this plan is **withdrawn**; `geo.metric_daily` supersedes it.

**Each Drizzle migration requires `src/lib/server/db/migration-contract.ts` updated in the same commit** (`tag`, `createdAt` = journal `when`, `sha256` of the new `.sql`). `/api/ready` fails the Railway healthcheck otherwise (`src/app/api/ready/route.ts:47-48`, `src/lib/server/db/migration-contract.ts:1-5`). Current head is `0007_governed_environmental_ingestion`.

### Where each of the 11 sources lands

Every row also mints or resolves `geo.geometry` rows; the "Geometry" column says of what kind.

| # | Source | Raw / provenance | Geometry | Model plane (`agri`) | Serving plane | Raster? |
|---|---|---|---|---|---|---|
| 1 | NASA FIRMS | `source_release` + `artifact` (R2 if >1 MB) | `point` | `signal_observation` (fire count per cell) | `geo.features` layer `fire-detections` | no |
| 2 | USGS NWIS streamflow | `source_release` | `point` | `signal_observation` (`streamflow_cfs`, `gauge_height_ft`) | `geo.features` layer `water-gauges` | no |
| 3 | Open-Meteo current | `source_release` | `point` | `signal_observation` (temp/precip/RH/wind) | `geo.features` layer `weather-observations` | no |
| 4 | WFIGS fire perimeters | `source_release` + `artifact` | `polygon` (Type-1, redrawn in place) | — (geometry, not a cell signal) | `geo.features` layer `fire-perimeters` | no |
| 5 | USDM drought | `source_release` + `artifact` (~19 MB → R2) | `polygon` | `drought_polygon_snapshot` (**already wired**) | `geo.drought_areas` + `geo.metric_daily` | no |
| 6 | NDVI (GIBS MODIS 8-day) | `source_release` + `artifact` (PMTiles → R2) | `grid_cell` via `cell_source_crosswalk` | `signal_observation` (`ndvi_mean` per cell) | R2 PMTiles + `/api/tiles/vegetation/ndvi/...` + `geo.metric_daily` | **yes** |
| 7 | SoilGrids | `source_release` + `artifact` (COG → R2) | `grid_cell` | `signal_observation` static features | keep `public.soil_grid_cache` as the point-read path | yes |
| 8 | Terrain (AWS terrarium DEM) | `source_release` + `artifact` (PMTiles → R2) | `grid_cell` | `signal_observation` (`elevation_m`, `slope_pct`, `aspect_deg`) | R2 PMTiles, `NEXT_PUBLIC_TERRAIN_URL` repointed | **yes** |
| 9 | NLCD | `source_release` + `artifact` (PMTiles → R2) | `grid_cell` | `signal_observation` (class fractions per cell) | R2 PMTiles replaces the MRLC WMS in `LandCoverLayer` | **yes** |
| 10 | LANDFIRE EVT | `source_release` + `artifact` (PMTiles → R2) | `grid_cell` | `signal_observation` (fuel-model params per cell) | R2 PMTiles replaces the identify-endpoint in `LandFireLayer` | **yes** |
| 11 | **MTBS burn severity (D3)** | `source_release` + `artifact` (paged GeoJSON → R2) | `polygon`, `natural_key = 'mtbs:<Fire_ID>'` | `signal_observation` per cell via `cell_source_crosswalk` (`spatial_support_kind='area_aggregate'`) | `geo.features` layer `burn-severity` | no |

---

## 3. Job-by-job port table

All six move together. Target module root: `services/agri-data-service/src/agri_data_service/ingest/`.

| Job | Current TS | Target Python module | CLI command | Upstream | Target table | Rows at time of writing¹ |
|---|---|---|---|---|---|---|
| nasa-firms | `ingestion-jobs.ts:118-168` `runFireIngestionJob` | `ingest/firms.py` | `ingest-firms` | FIRMS area CSV/GeoJSON (`services/nasa-firms.ts`) | `geo.features` (`fire-detections`) | **6297** |
| usgs-streamflow | `ingestion-jobs.ts:171-208` `runWaterDroughtIngestionJob` | `ingest/usgs_nwis.py` | `ingest-streamflow` | USGS NWIS IV (`services/usgs-water.ts`) | `geo.features` (`water-gauges`) | **7596** |
| open-meteo | `ingestion-jobs.ts:264-310` `runWeatherIngestionJob` | `ingest/open_meteo.py` | `ingest-weather` | Open-Meteo current (`services/weather.ts`) | `geo.features` (`weather-observations`) | **1013** |
| wfigs-fire-perimeters | `ingestion-jobs.ts:313-360` `runFirePerimetersIngestionJob` | `ingest/wfigs.py` | `ingest-fire-perimeters` | `services3.arcgis.com/.../WFIGS_Interagency_Perimeters_Current` (`wfigs-fire-perimeters.ts:29`) | `geo.features` (`fire-perimeters`) | **110** |
| usdm-drought | `ingestion-jobs.ts:368-377` → `drought-ingestion.ts:103-130` | `ingest/usdm.py` | `ingest-drought` | `droughtmonitor.unl.edu/data/json/usdm_<YYYYMMDD>.json` (`usdm-drought.ts:28`) | `geo.drought_areas` | **5** (1 release × 5 DM classes) |
| ndvi | `ingestion-jobs.ts:380-388` — **stub, always skips** ("No versioned warehouse-backed NDVI adapter is configured") | `ingest/ndvi.py` | `ingest-ndvi` | GIBS `MODIS_Terra_NDVI_8Day` (`src/lib/vegetation.ts:24`) | R2 PMTiles + `signal_observation` | 0 |

Plus one orchestrator: `ingest-all` — runs the six, isolates failures per job (matching `runAllIngestionJobs`'s `Promise.all` + per-job try/catch at `ingestion-jobs.ts:407-421`), prints a JSON summary, **exits non-zero if any job failed**.

¹ **Orientation only — these numbers are already wrong.** The cron writes continuously; water-gauges and weather-observations moved by +2 244 and +485 during the drafting of this plan. Never assert against them (§3 verification step 2).

**Write `ingest/identity.py` first, before any job module.** One module, four pure functions, six callers, no other place in the Python tree is allowed to build an identity string. This is what makes Phase 3 a lift-and-shift instead of a rewrite: the dimension's `natural_key` is `f"{producer}:{identity.build(...)}"`, and the Phase 3 backfill is proven correct by the same golden-file fixtures that gate Phase 1. Porting the six jobs without this module first means writing the six-way duplication a second time in a second language.

### Behaviours the port must replicate exactly

These are the correctness traps. Each is a real, load-bearing detail in the TS.

| # | Behaviour | Source | Consequence if missed |
|---|---|---|---|
| 1 | **Feature-ID format is the dedupe key.** FIRMS = `satellite:acqDate:acqTime:lat(4dp):lon(4dp)` (`ingestion-jobs.ts:100-115`); gauges = `${siteNo}:${updatedAt}` (`:189`); weather = `lat(4dp):lon(4dp):observedAt` (`:294`); perimeters = `uniqueFireIdentifier` (`:335`). Stored as `properties->>'id'` | `ingest.ts:81` | **Silent duplication of every existing row** (~15 k and growing hourly). Highest-severity risk until Phase 3 lands. |
| 2 | Layer reference resolves name → UUID against `geo.layers` | `ingest.ts:17-33` | FK violation or wrong layer |
| 3 | Advisory transaction lock over sorted `layerId:eventId` keys before the existence check | `ingest.ts:46-54` | concurrent-run races |
| 4 | Refresh-in-place diff **ignores `geometry` and `geometry_repaired`** — the `drizzle/0004` trigger rewrites `properties.geometry` via `ST_AsGeoJSON`, so a whole-payload compare rewrites every row every run | `ingest.ts:95-122` | endless row churn + realtime storm |
| 5 | Redis publish per written row to `layer:<name>` | `ingest.ts:163-172` | map loses live invalidation |
| 6 | `INGEST_BBOX` policy: `west,south,east,north`, `east-west ≤ 30°`, `north-south ≤ 20°` | `ingestion-jobs.ts:70-94` | unbounded fetches |
| 7 | Weather grid is **densified, never sliced** — spacing scales up until ≤150 points | `ingestion-jobs.ts:233-261`, `:27` | uneven coverage |
| 8 | FIRMS freshness rejection at `min(10, max(1, dayRange))` days | `ingestion-jobs.ts:136-141` | stale detections |
| 9 | USDM: date is the *request parameter*, never parsed from payload; `usdm_current.json` deliberately unused; 404 = "not published yet"; duplicate DM class ⇒ reject whole release | `usdm-drought.ts:96-159` | mis-dated releases |
| 10 | USDM geometry repaired in-DB: `ST_Multi(ST_CollectionExtract(ST_MakeValid(...), 3))`, `ON CONFLICT (valid_date, dm_category) DO UPDATE ... WHERE <replace>` | `drought-ingestion.ts:55-74` | unreliable `ST_Intersects` |
| 11 | USDM prune keeps newest N releases (default 8, ~19 MB each) | `drought-ingestion.ts:82-97` | unbounded growth |
| 12 | Perimeter severity from `percentContained`, **`null` when upstream reports nothing** — never defaults to lowest severity | `ingestion-jobs.ts:215-226` | map renders wrong colour |

### Verification method

Run against the live database, in this order, per job:

1. **Golden-file ID test (before any write).** Feed the recorded TS output for a captured upstream payload into the Python ID builder; assert byte-identical `featureId` strings. This gate must pass before the job is allowed to connect to the DB at all.
2. **Idempotence.** `SELECT count(*) FROM geo.features f JOIN geo.layers l ON l.id=f.layer_id WHERE l.name=$1` **captured immediately before** the run, then again after, against a *frozen replayed payload*. Delta must be **exactly 0**.
   **Never assert against a baseline written into a document, this one included.** Ingestion runs on a cron, so any literal goes stale within hours — the four layers moved from 6297 / 5352 / 528 / 110 to 6297 / **7596** / **1013** / 110 during the drafting of this plan. The test captures its own left-hand side or it is measuring the clock, not the code. (Current counts are recorded in §2.0 for orientation only; they will be wrong by the time anyone reads them.)
3. **Content diff.** For 200 sampled `properties->>'id'` per layer, diff the full `properties` JSONB minus `geometry`/`geometry_repaired` against the pre-run snapshot. Zero differences.
4. **Live-run delta plausibility.** Run against real upstream; the delta must be non-negative and within the historical hourly range for that feed. A run that writes a whole layer's worth of new rows is trap #1 firing.
5. **Realtime.** Subscribe to `layer:<name>` and assert one message per written row.
6. **Cutover.** Run the TS route and the Python CLI against the same frozen payload into two databases and diff the whole layer.

---

## 4. The five new in-house sources

PNW coverage area from the existing constants: **`-125,42,-111,49`** (`src/__tests__/services/ingestion-jobs.test.ts:3`); the map default viewport is documented as matching it (`src/stores/map-store.ts:28-36`). ≈ 1100 km × 780 km ≈ **857 000 km²**. All storage figures below are for that box.

| | **Soil — SoilGrids** | **Terrain — DEM** | **NLCD** | **LANDFIRE** |
|---|---|---|---|---|
| Upstream today | `rest.isric.org/soilgrids/v2.0/properties/query` per-point (`soilgrids.ts:45`) | `s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` (`sources.ts:3-5`) | `mrlc.gov/geoserver/mrlc_display` WMS (`nlcd.ts`) | `landfire.gov/arcgis/.../Landfire/US_200/MapServer/identify` (`landfire.ts:52`) |
| In-house upstream | ISRIC bulk VRT/COG, 250 m, 6 properties @ 0-5 cm | same S3 bucket, bulk tile pull | MRLC national GeoTIFF, 30 m | LANDFIRE national GeoTIFF (EVT), 30 m |
| Licensing | CC-BY 4.0 — redistribution permitted with attribution | ODbL/public-domain mix (SRTM/NED/GMTED); attribution required | US federal, public domain | US federal, public domain |
| Bounding | clip to PNW bbox at source | **bbox-limit to PNW**, z0-z12 only | clip to PNW bbox | clip to PNW bbox |
| Raw size | ~330 MB (6 props × 250 m × float32) | ~1-2 GB PMTiles (~23 000 tiles z0-12) | ~950 MB uncompressed, ~150-300 MB as categorical PMTiles | ~950 MB uncompressed, ~150-300 MB as PMTiles |
| **Does it grow?** | **No** — SoilGrids v2.0 is a static release (`soilgrids.ts:56`) | **No** — one-time archive | **No per year** — NLCD ships every 2-3 y (current: 2021) | **No per year** — LF versioned releases (current: US_200) |
| Retention | keep all versions; ~330 MB each, maybe one new every few years | keep one; replace on DEM refresh | keep current + one prior | keep current + one prior |
| Storage 5-yr projection | < 1 GB | ~2 GB | < 1 GB | < 1 GB |
| Target | R2 COG + `signal_observation` static features; `public.soil_grid_cache` stays as the fast point path | R2 PMTiles; repoint `NEXT_PUBLIC_TERRAIN_URL` | R2 PMTiles; `LandCoverLayer` reads it | R2 PMTiles; `LandFireLayer` reads it |

### MTBS burn severity (D3) — the fifth

Different shape from the other four: vector, not raster; annual, not static; and it already has a live call site to retire.

| | **MTBS burn severity** |
|---|---|
| Upstream today | `services3.arcgis.com/T4QMspbfLg3qTGWY/.../MTBS_Polygons_v1/FeatureServer/0/query` — a **live, request-time** call, Redis-cached only (`src/lib/server/services/mtbs.ts:22,30-33`) |
| In-house upstream | same endpoint, but **paged** with `resultOffset` until exhausted. Today's call passes `resultRecordCount: "500"` (`mtbs.ts:50`) with no paging, so **any bbox with more than 500 fires is silently truncated** — persisting it fixes a live correctness bug, not only a persistence gap |
| Bounding | PNW bbox `-125,42,-111,49` as an `esriGeometryEnvelope` (already the shape of the current query, `mtbs.ts:43-46`); optional `Ig_Year` floor to cap the first pull |
| Cadence / growth | annual — MTBS publishes the previous calendar year's fires, typically 12-18 months in arrears. One full historical pull, then one refresh per year |
| Raw size | CONUS all-time is ~30 k polygons; the PNW share is ~3-5 k. **~5-20 MB per annual GeoJSON release**, over the 1 MB threshold ⇒ R2 artifact |
| Storage 5-yr projection | **< 150 MB** total including every annual release. Negligible |
| Retention | **keep every annual release.** Each is a distinct versioned scientific product and re-derivation is not possible if the service changes |
| Geometry | `geo.geometry`, `geom_kind='polygon'`, `natural_key='mtbs:<Fire_ID>'`. Type-1: MTBS revises perimeters between annual releases under a stable `Fire_ID` |
| Model plane | per-cell `burn_severity_class` / `burned_area_ac` via `agri.cell_source_crosswalk` with `spatial_support_kind='area_aggregate'` — severity is a per-fire attribute, so it must be areally apportioned onto cells, which is exactly what `coverage_fraction` is for |
| Serving plane | `geo.features` layer `burn-severity`; `BurnHistoryLayer.tsx` repoints off the live call |
| `data_available_at` | the **release publication date** of the MTBS annual product, *not* `Ig_Date`. A 2023 fire only became knowable when the 2024/25 release shipped; using the ignition date would leak roughly 18 months |

**Licensing — recorded as an open item, deliberately not resolved.** The MTBS data itself is a joint USGS/USFS federal product and is normally public domain, but we would be bulk-pulling it through an **Esri-hosted ArcGIS Online feature service**, whose hosting terms are a separate instrument from the data's own terms. That is the same host family as the WFIGS perimeters we already persist (Appendix A1), which is a reasonable precedent but not a legal opinion. Owner's call: *"we can figure out licensing or whatever later."* Persist it now; carry it as open question 5.

**And NDVI — the one that grows.** GIBS `MODIS_Terra_NDVI_8Day` at 250 m over the PNW box is ~1.37 × 10⁷ pixels ⇒ **~14 MB/date raw, ~30-50 MB/date as a PMTiles pyramid**. At 46 composites/year that is **~1.5-2.5 GB/year**, growing linearly and forever.

Retention policy for NDVI, explicitly:

| Artefact | Retention | Rationale |
|---|---|---|
| Per-cell `ndvi_mean` scalars in `signal_observation` | **forever** | tiny (bytes/cell/date); this is the model feature |
| Raster PMTiles on R2 | **rolling 24 months**, prune older by `source_release.observed_to` | the map only ever shows recent dates; older rasters are re-derivable from GIBS on demand |

Honest summary of growth: **NDVI is the only source in this plan with unbounded growth, and its bounded part (the scalars the model actually needs) is negligible. Soil, terrain, NLCD and LANDFIRE are one-time or multi-year static pulls that do not grow; MTBS grows ~20 MB/year.** Total steady-state R2 footprint after 2 years ≈ 8-10 GB ≈ **$0.15/month**. Storage is not the constraint; ingestion compute and babysitting are.

---

## 5. Feature store link — the leakage question

**A forecast for time T may only use data knowable at T.** This is already modelled and enforced. The work is extension, not invention.

### What already exists

| Mechanism | Where | What it guarantees |
|---|---|---|
| `signal_observation.data_available_at` distinct from `observed_at` | `20260720_0002:139-142` | a value carries *when it was measured* and *when it became knowable* |
| `agri.covariate_daily_features(cell_id, window_start, window_end, **as_of_time**, schema_version)` filters `signal.data_available_at <= p_as_of_time` | `db/agri/functions/covariate_daily_features.sql` | **the leakage gate**. A feature vector built at `as_of_time` physically cannot contain a value published later. |
| `DISTINCT ON (signal_name, day) ... ORDER BY data_available_at DESC` | same file | revisions are handled: the latest *admissible* release wins; a rolling window can never double-count a re-ingested day |
| `agri.covariate_feature_schema('agri_covariates_v1')` → 40 features | `db/agri/functions/covariate_feature_schema.sql` | 7 met signals × {lag_1, lag_2, lag_3, roll_mean_7, roll_mean_28} = 35, + 3 drought (`lag_1`, `lag_7`, `imputed_lag_1`), + 2 calendar (doy sin/cos). Streams: `nasa_power`, `usdm`, `calendar`. |
| `agri.covariate_lookback_days()` = `max(lag_days + window_days - 1)` = **28** | `covariate_lookback_days.sql` | the history window is derived from the schema, not hardcoded |
| `agri.covariate_declared_gap()` | `covariate_declared_gap.sql` | a planned-but-unqueried stream is recorded as an **explicit typed gap** (`era5_land` / `credential_gated`), *never* as empty columns implying queried-and-absent |
| `agri.drought_class_daily_series(cell, from, to, as_of)` | `20260802_0016_assignment_time_covariate_layer.py:29` | weekly USDM polygons → a daily per-cell severity spine, itself as-of gated, with an `is_imputed` flag surfaced as feature 38 |
| `forecast_hindcast_run.actual_knowledge_as_of` | `20260801_0014:157-185` | the actuals-read horizon is pinned **once at finalization**, so an evaluation replays identically instead of drifting with `clock_timestamp()` |

The assignment-time covariate layer is `agri_covariates_v1`, added 2026-08-02 by `20260802_0016`. **Build on it. Do not duplicate it.**

### What is missing

Only two things, and neither is architectural.

**(a) The new sources are not in the feature schema.** `v1` covers `nasa_power`, `usdm`, `calendar`. Nothing else. Adding a source = an Alembic migration that bumps to `agri_covariates_v2` and returns extra rows from `covariate_feature_schema`, plus extending the `covariate_daily_features` body with the matching join. Proposed `v2` additions:

| Feature block | Stream key | Kind | Shape |
|---|---|---|---|
| streamflow (mean, 7-day anomaly) | `usgs_nwis` | `hydrology` | `lag_1`, `roll_mean_7`, `roll_mean_28` |
| NDVI mean + 8-day delta | `gibs_modis_ndvi` | `vegetation` | `lag_8`, `lag_16` (composite cadence — **not** `lag_1`) |
| observed weather (temp/precip/RH/wind) | `open_meteo` | `meteorology` | mirrors the `nasa_power` shapes |
| fire-detection count in cell | `nasa_firms` | `fire` | `roll_sum_1`, `roll_sum_7` |
| soil pH, SOC, N, bulk density, CEC, OCD | `soilgrids` | `static` | `lag_days = 0`, `window_days = 1` |
| elevation, slope, aspect | `terrain` | `static` | same |
| NLCD class fractions | `nlcd` | `static` | same |
| LANDFIRE EVT fuel params | `landfire` | `static` | same |
| burn severity / years-since-burn | `mtbs` | `fire` | `lag_days = 0`, `window_days = 1` — annual product, treated as a slow-moving static |

Statics still carry a real `data_available_at` = their `source_release.data_available_at`. A 2021 NLCD release is knowable for any `as_of_time` after its publication and **not before** — so a 2019 hindcast correctly sees no land cover, and the gate handles that for free. Do not shortcut this by treating statics as always-available; that is exactly the leak.

**(b) `data_available_at` must be set honestly per source, at ingest.** This is the single place a solo dev can silently poison every downstream model. It is **not** "when we fetched it" and **not** `observed_at`:

| Source | `observed_at` | `data_available_at` |
|---|---|---|
| USDM | release `valid_date` (a Tuesday) | the **Thursday** it is published — USDM publishes Thursdays for the preceding Tuesday (`usdm-drought.ts:32`). Using Tuesday leaks 2 days. |
| NDVI (8-day composite) | composite **end** date | composite end + GIBS publication latency (typ. 1-3 d) |
| Open-Meteo current | `weather.observedAt` | ≈ same (near-real-time) |
| USGS NWIS | gauge `updatedAt` | ≈ same |
| FIRMS | `acqDate`+`acqTime` (`environmental-time.ts`) | acquisition + FIRMS NRT latency (~3 h) |
| Statics (soil/terrain/NLCD/LANDFIRE) | release epoch | release publication date |
| MTBS | fire `Ig_Date` | the **annual release publication date** (typ. 12-18 months after ignition). Using `Ig_Date` leaks ~18 months — the largest single leak available in this plan. |

**Enforcement:** each new ingest module declares its availability rule as a pure function `(payload_record) -> data_available_at` with unit tests, and the value is written to `signal_observation.data_available_at`. No ingest may default it to `now()`, **and none may read it from `geo.features.created_at`** — measured, that column is rewritten on every refresh-in-place (`src/lib/server/services/ingest.ts:107-122`), so all 15 016 rows report as created today regardless of when the observation happened. It is a write timestamp wearing a creation timestamp's name, and it would look entirely reasonable in a diff. Where the rule is genuinely unknown (e.g. exact GIBS latency for a given product), use `covariate_declared_gap` to record the uncertainty rather than guessing — that mechanism exists precisely for this.

---

## 6. The time slider (D2)

The map's temporal control is **one continuous, day-granular slider** running from the earliest observation, through today, to today + 30. Selecting any day re-renders the active layers at that date. There are no "5 d / 10 d / 30 d" buttons — a horizon button is just a slider position, and having both is two controls for one concept.

### Where computed

**Batch CLI, persisted. Never at request time.** Monte Carlo with 1000 paths × 30 horizons × N cells is seconds-to-minutes of PostgreSQL work — acceptable in a nightly cron container, unacceptable inside an HTTP handler (that is the mistake `/api/cron/ingest` already makes). The map only ever reads `geo.metric_daily`.

`forecast_iteration.horizon_days` is CHECK-constrained to 1-366 with `expected_value_count = horizon_days` (`20260723_0010:1142-1146`). **Run one iteration at `horizon_days = 30`.** The slider slices it; the batch does not.

### The uniform read is the whole design

The slider is only simple if "value at (`geometry_id`, `valid_on`, `metric_name`)" answers the same way on both sides of today. §2.1 is where that is delivered: one `geo.metric_daily` table, `value_kind` as a column, observed and forecast unioned **by the batch projector at write time**, read by one indexed `DISTINCT ON` query. The tRPC procedure `environmental.getMetricAtDate({ metric, date, variant, bbox })` is the single read path for every slider position.

The alternative — two tables and a `UNION ALL` view — was rejected because the branch would then also exist in every cache key, every legend and every popup, and one of those copies will eventually disagree about where "today" is.

| Slider position | `value_kind` | `variant` | Band | Variant toggle |
|---|---|---|---|---|
| any past day | `observed` | `observed` | none (`low`/`high` NULL) | disabled, greyed, with a reason |
| today | `observed` | `observed` | none | disabled |
| today + 1 … + 30 | `forecast` | `monte_carlo` \| `ml` | shown | **enabled** |

### Both variants already have a lane — reuse, do not duplicate

| Variant | Engine | Existing machinery | Band columns |
|---|---|---|---|
| **Statistical Monte Carlo** | `agri.forecast_daily_bootstrap(...)` — seeded, `simulation_count` 100-10 000, hash-driven sampling over daily increments (`20260723_0010:800-1096`) | `forecast_iteration` → `forecast_iteration_value` (`db/agri/tables/forecast_iteration_value.sql:7-24`) | `low_value` / `median_value` / `high_value` per `horizon_step`, already `CHECK (low <= median <= high)` |
| **ML** | trained model → `v_forecast_series_serving` where `forecast_method = 'ml'` | `agri.mv_forecast_ml_daily_serving` (`db/agri/materialized_views/mv_forecast_ml_daily_serving.sql:7-44`), refreshed by CLI `forecast-refresh-ml-daily` (`cli.py:280-295`) | `lower_p10_value` / `median_p50_value` / `upper_p90_value`, already daily |

Both lanes already emit a three-number band, so the projection into `geo.metric_daily` is a column rename, not a new uncertainty representation.

> **Blocking caveat — read this before Phase 2.** `mv_forecast_ml_daily_serving` is built on `agri.v_forecast_series_serving`, which joins `publication_pointer → forecast_publication → forecast_publication_item → forecast_receipt → forecast_value` (`db/agri/views/v_forecast_series_serving.sql:53-59`) and keys on `(publication_id, forecast_receipt_id, series_id, valid_day)` (`mv_forecast_ml_daily_serving.sql:47`). **Cutting the receipt/publication planes deletes the ML lane.** See risk 3 and open question 3.

### Per-layer forecast capability — the slider must not lie

**Not every layer is forecastable.** Drought and vegetation are continuous per-cell quantities with a history to bootstrap from. Fire detections are discrete events: there is no "FIRMS value on 2026-08-20", and rendering one would be fabrication. The slider therefore has to know, per layer, how far it may travel.

Three columns on `geo.layers` (Drizzle; the table already exists at `src/lib/server/db/schema.ts:150-163`):

```sql
ALTER TABLE geo.layers
  ADD COLUMN temporal_kind         varchar(16)  NOT NULL DEFAULT 'snapshot',
      -- 'snapshot'    : one current state, no history        (e.g. soil, terrain)
      -- 'daily_series': a value per day                      (drought, NDVI, weather)
      -- 'event'       : discrete occurrences, never forecast (FIRMS, WFIGS, MTBS)
  ADD COLUMN forecast_horizon_days smallint     NOT NULL DEFAULT 0,   -- 0 = not forecastable
  ADD COLUMN forecast_variants     varchar(24)[] NOT NULL DEFAULT '{}';
```

Behaviour, and it must be **visible, not silent**:

- **Slider domain** = `[min observed day across active layers, today + max(forecast_horizon_days)]`. The future segment of the track is rendered hatched, with a labelled tick at today.
- **A layer outside its own range greys out and says why in the legend** — "Fire detections are events; no forecast exists" — it does **not** silently disappear, and it does **not** keep rendering today's data under a future date label. Silently freezing an `event` layer at its last value while the slider says "+12 days" is the specific failure this flag exists to prevent.
- `forecast_variants` drives which toggle options are selectable, so a layer with a Monte Carlo lane but no trained model shows ML as unavailable rather than empty.
- The toggle is disabled entirely for `date <= today`, with the tooltip "forecasts apply to future dates".

### Showing uncertainty on a chosen day without implying false precision

The bands exist; the map must not flatten them into a crisp choropleth.

| Channel | Encoding |
|---|---|
| Colour | `median_value` on the metric's ramp — same ramp as observed days, so the eye compares like with like |
| Opacity | `(high_value - low_value)` normalised across the visible extent: **wide band ⇒ washed out** |
| Outline | observed days draw a **solid** boundary; forecast days draw a **soft/dashed** one. A crisp edge is the visual claim "we know exactly where this ends" |
| Popup | `low` / `median` / `high`, the `issued_on` date, the variant, and `provenance_key` |
| Legend | when the slider is in the future, the legend gains a band-width key alongside the colour ramp |

Two prohibitions: **no isolines or contouring on forecast days** (a contour is a precision claim the band does not support), and **no interpolation between slider days** — day 7 and day 8 are separate rows; animating between them invents values.

### Serving path

```
nightly:  forecast-run-serving --horizon-days 30 --purpose serving   (existing forecast-run-iteration)
          forecast-refresh-ml-daily                                   (existing, cli.py:280)
          observations-project-serving  ← NEW: agri/geo observations → geo.metric_daily
          forecast-project-serving      ← NEW: both forecast lanes    → geo.metric_daily
          metric-daily-prune            ← NEW: collapse issues older than 14 d
```

`forecast-project-serving` reads both lanes, normalises to `(geometry_id, metric_name, valid_on, issued_on, variant, low, median, high)`, and upserts on the `geo.metric_daily` primary key. **It is the only place the two lanes are unified**, it filters `purpose = 'serving'` if the D4 recommendation is accepted, and it is ~120 lines.

### Front-end work — what actually changes

Map layers today fetch "latest" with no date parameter anywhere. Concretely:

| Site | Today | After |
|---|---|---|
| `src/stores/map-store.ts:7-25` | `MapState` has **no time field at all** — viewport, layers, style, terrain only | +2 fields (`selectedDate`, `forecastVariant`), +2 actions |
| `src/components/map/LayerManager.tsx` | 4 tRPC calls with no date: `environmental.getDroughtClassification`, `environmental.getStreamflow`, `environmental.getGroundwater`, `wildfire.getWeatherForBbox` | each takes `date`; each checks the layer's `temporal_kind` / `forecast_horizon_days` first |
| `src/lib/server/services/environmental-read-model.ts:411-416` and `:530-535` | two hardcoded `WITH latest AS (SELECT valid_date … ORDER BY valid_date DESC LIMIT 1)` subqueries | `WHERE valid_date <= $date ORDER BY valid_date DESC LIMIT 1` — as-of, not latest |
| `src/components/map/layers/VegetationLayer.tsx:67-68,84` | already accepts `year`/`month` props but templates a `.../vegetation/nbr/latest/{z}/{x}/{y}.png` URL | date-templated tile path — **the cheapest layer to convert, and the precedent for the rest** |
| Martin MVT functions | `(z, x, y)` only — `infra/martin/martin.yaml:29-40`, definitions at `drizzle/0001_handy_riptide.sql:376,412,448,485`. **No date can be passed.** | unchanged; the slider layer serves GeoJSON via tRPC instead (§2.1) |
| — | — | **new:** `TimeSlider` component, one tRPC procedure `getMetricAtDate`, legend band key |

**Net: 2 store fields, 4 client call sites, 2 server "latest" subqueries, 1 raster URL template, 1 new component, 1 new procedure.** Every existing MVT tile function is untouched.

**Cache-key consequence, flagged:** Redis keys go from one per layer ("latest") to one per layer **per day**. A 400-day observed window plus 30 forecast days is ~430 keys per layer per variant. Bound it: cache only the ±7-day neighbourhood of the current selection with a short TTL, and let older days fall through to Postgres. Do not naively key on every date.

**Do not** build a "forecast comparison" view, a skill-score dashboard, or a model-registry UI in this plan. The evaluation harness lives in `agri` (`forecast_backtest_metric`, `forecast_quality_policy`); it is a CLI concern.

---

## 7. Phasing

Each phase ships alone and is worth having alone.

**Hard ordering constraint:** Phases 2 and 3 both make destructive schema changes to `agri`, which is free only while it holds **0 rows**. Phase 4 writes the first row. Do not reorder 2, 3 and 4.

### Phase 1 — Port the six jobs; swap the cron container
`ingest/identity.py` first, then six Python ingest modules + `ingest-all`; rebuild `infra/cron-ingest` from the Python image; delete `/api/cron/ingest`.
**Value:** a failed fetch becomes a red Railway run instead of a log line nobody reads.
**Zero schema changes** — no Drizzle migration, no Alembic migration. Targets already exist.

### Phase 2 — Cut the enforcement layer; remove the eval-only lock (D4)
One or two Alembic revisions: drop `finalize_*`, the `guard_*` triggers, the evidence CHECKs and the 4 owner roles; convert the two `GENERATED … STORED` `value_checksum` columns to plain columns; drop `ck_forecast_iteration_method` and `ck_forecast_iteration_purpose` and flip the `purpose` default to `'serving'`. Keep every checksum column, the 11 checksum functions and `materialize_forecast_iteration`'s idempotency block.
**Value:** the warehouse restores from its own `pg_dump` again, schema changes stop needing a `DISABLE TRIGGER` bracket, and a serving forecast becomes legal to write.
**Free only right now** — 0 rows means no data migration and no backfill. Resolve open question 3 (the ML lane's spine) inside this phase, not after it.

### Phase 3 — `geo.geometry` conformed dimension; backfill; repoint the facts (D1)
Drizzle: `geo.geometry` + `migration-contract.ts`. Backfill from `geo.features` behind a census and a two-sided row-count assertion. Alembic: repoint `signal_observation.cell_id`, `forecast_series.spatial_cell_id` and `cell_source_crosswalk.cell_id` to `geometry_id`; retire `agri.spatial_cell`. Add the Drizzle-before-Alembic note to `services/agri-data-service/db/AGENTS.md`.
**Value:** identity is defined once. Six scattered dedupe rules and a per-layer partial index collapse into one UNIQUE `natural_key`, which is the structural fix for the plan's own critical risk.
Existing MVT tile functions are untouched.

### Phase 4 — Provenance on the operational lane
Every fetch writes `agri.source_release` (+ `artifact` for payloads > 1 MB, uploaded to R2) before touching `geo`.
**Value:** every value on the map traces to a checksummed, licensed, timestamped release, and the raw bytes are retrievable — so `geo` can be re-derived after a bad run without re-fetching.

### Phase 5 — Five new in-house sources + NDVI
Bulk-pull soil / terrain / NLCD / LANDFIRE to R2 PMTiles+COG within the PNW bbox; page and persist MTBS (D3); fill the NDVI stub; repoint the five front-end layers off third-party endpoints.
**Value:** five panels render from our own CDN instead of third-party endpoints, NDVI works for the first time (it has always been a stub, `ingestion-jobs.ts:380-388`), and MTBS stops silently truncating at 500 features (`mtbs.ts:50`).

### Phase 6 — Narrow fact + covariates v2 + Monte Carlo + the time slider (D2)
Alembic: `agri_covariates_v2`. Drizzle: `geo.metric_daily`, the three `geo.layers` capability columns, `migration-contract.ts`. CLI: `observations-project-serving`, `forecast-project-serving`, `metric-daily-prune`. Front end: `TimeSlider`, `getMetricAtDate`, date-threaded reads, band rendering (ML option present but disabled).
**Value:** the headline — scrub any day from the start of history to +30 and see the map at that date, with observed values behind today and an honest uncertainty band ahead of it.

### Phase 7 — ML variant on the slider toggle
Train against `covariate_daily_features` at `agri_covariates_v2`; publish through whichever ML lane open question 3 settles on; enable the toggle for future dates; run hindcast evaluation to compare the two.
**Value:** the toggle gets its second option, and there is a defensible answer to "is the ML better than the bootstrap?"
The only phase whose value depends on another (Phase 6).

---

## 8. Risks and non-goals

### Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Dimension identity collision (Phase 3).** `geo.geometry.natural_key` is globally UNIQUE, but today's ids are unique only *within a layer* (`features_layer_external_id_unique`, `src/lib/server/db/schema.ts:181-184`). An unnamespaced key merges two unrelated facts onto one geometry — silently and irreversibly | **Critical** | `natural_key` is always `'<producer>:<producer-local id>'`. Assert `eligible == inserted` inside the backfill transaction, before COMMIT (§2.0). Rehearse on a DB snapshot. *Measured and no longer a concern: `properties->>'id'` is non-null on **all** 15 016 rows, so there is no unkeyed remainder to reconcile.* |
| 2 | **Feature-ID drift (Phases 1-3).** Python generating subtly different id strings (float formatting, timezone rendering, `toFixed(4)` vs `f"{x:.4f}"`) duplicates every existing row, because dedupe is entirely `properties->>'id'` (`ingest.ts:56-65`) | **High** *(was Critical)* | Golden-file ID test gates DB access (§3 step 1); idempotence asserts a delta of exactly 0 on frozen replay **against a baseline captured in the same transaction**, never a literal. **Character changed:** after Phase 3 the format is defined once in `ingest/identity.py` and stored once as `natural_key`, so drift becomes structurally impossible rather than test-suppressed. Bounded to Phases 1-3. |
| 2b | **Stale hardcoded row counts.** Ingestion runs on a cron, so any count written into a test, a fixture or this document is wrong within hours — the four layers moved by ~2.7 k rows during the drafting of this revision. A test asserting against a literal fails spuriously, and the reflex fix ("update the number") hides the next real regression | Medium | Every count assertion captures its own baseline immediately before the run. No literal row counts in test code. The figures in §3 and §2.0 are labelled orientation-only. |
| 2c | **`created_at` is "last touched", not "first seen."** All 15 016 rows read as created today because the refresh-in-place path rewrites them (`src/lib/server/services/ingest.ts:107-122`). Anything treating it as a first-observation timestamp — `data_available_at`, the slider's history depth, retention windows, "new since" queries — is silently wrong, and wrong in the direction that looks plausible | **High** | `geo.geometry.first_seen_at` is set on INSERT only and is **not** backfilled from `created_at`. `data_available_at` comes from the per-source availability rule (§5b), never from a write timestamp. Slider history depth comes from `geo.metric_daily.valid_on`. Audit for other `created_at` readers before Phase 6. |
| 3 | **Cutting the receipt/publication planes deletes the ML serving lane.** `mv_forecast_ml_daily_serving` → `v_forecast_series_serving` → `publication_pointer / forecast_publication / forecast_publication_item / forecast_receipt / forecast_value` (`db/agri/views/v_forecast_series_serving.sql:53-59`). D2 says reuse that matview; "cut the planes" removes its spine. The checksum audit did not measure this | **High** | Decide in Phase 2, not after. Two options in open question 3. Mitigating fact: **no Python code writes `forecast_receipt` or `forecast_publication` today** — only ORM class definitions (`models/forecasting.py:852,903,941`) and the undeployed `routes/forecasts.py` — so the ML lane is not wired end-to-end regardless, and rebuilding it narrow is cheap now and expensive later. |
| 4 | **`data_available_at` set to `now()`.** One lazy ingest module poisons every downstream model with lookahead, and it is invisible — the forecast just looks suspiciously good. MTBS makes this worse: using `Ig_Date` instead of the annual release date leaks ~18 months | **High** | No ingest may default it. Per-source availability rules are pure functions with unit tests (§5b). Batch-runner assertion: reject a `source_release` whose `data_available_at` is within 60 s of `now()` for any source that is not near-real-time. |
| 5 | **Drizzle-must-run-before-Alembic on a fresh database.** Cross-schema FK `agri.* → geo.geometry` (Phase 3). `preDeployCommand` runs Drizzle automatically; Alembic is manual, so the ordering is invisible until someone builds a new environment | Medium | The Alembic revision opens with `SELECT to_regclass('geo.geometry')` and raises a readable error rather than a bare FK failure. Document it in `services/agri-data-service/db/AGENTS.md` in the Phase 3 commit. Precedent for cross-schema FKs exists: `geo.layers.team_id → public.teams.id` (`schema.ts:159`). |
| 6 | **Slider read amplification (Phase 6).** Every layer × every day is a distinct query and a distinct cache key; naive keying turns 1 Redis key per layer into ~430 | Medium | Cache only the ±7-day neighbourhood of the current selection, short TTL, older days fall through to Postgres. Debounce slider drag; fetch on settle, not on every tick. Measure p95 on `getMetricAtDate` before adding a matview (§2.1). |
| 7 | **Type-1 dimension vs. a historical slider.** A redrawn WFIGS perimeter overwrites `geom` in place, so scrubbing to last week renders **today's** shape under last week's date — a silent lie, and exactly the class of error D2 exists to avoid | Medium | Open question 4. Cheapest honest fix: mark `producer='wfigs'` geometries as `temporal_kind='event'` on their layer so the slider does not claim historical fidelity for them; a Type-2 dimension is a much larger change and is not proposed here. |
| 8 | **Migration-contract coupling.** Phases 3 and 6 each add a Drizzle migration and each breaks the deploy unless `src/lib/server/db/migration-contract.ts:1-5` is updated in the same commit — `/api/ready` checks `drizzle.__drizzle_migrations` for the exact `created_at`+`hash` (`src/app/api/ready/route.ts:47-48`) and the Railway healthcheck kills the release | Medium | Same-commit rule; `src/__tests__/security/readiness-migration-contract.test.ts` already catches it in CI, which runs inside the Docker build (`Dockerfile:64-69`). Fires twice now, not once. |
| 9 | **Two migration tools on one database.** Alembic and Drizzle both migrate `plantgeo` | Medium | Ownership rule: Drizzle owns DDL for `geo`/`public`/`tracking`, Alembic owns `agri`, Python does DML only into `geo`. Neither tool's autogenerate is ever run against the other's schema. |
| 10 | Bulk raster processing (Phase 5) is real local compute — hours of babysitting, not a cron job | Medium | One-time per source; run locally, upload the result. Do not put raster processing in the hourly container. |
| 11 | **Storage growth.** NDVI rasters grow ~1.5-2.5 GB/year, forever | Low | 24-month rolling prune on rasters; scalars kept forever and negligible. 2-year footprint ≈ 8-10 GB ≈ $0.15/mo on R2. |
| 12 | **A hindcast surfaces on the map as a live forecast** — only if the D4 `purpose`-column recommendation is declined | Low | Accept the recommendation (one WHERE clause in two places, §2 DDL sketch A). If declined: `geo.metric_daily` is a projection, so the fix is re-running the projector, not editing rows. |
| 13 | **MTBS licensing.** Bulk-pulling a federal product through an Esri-hosted AGOL feature service | Low, **open** | Owner deferred it explicitly (D3). Carried as open question 5; the data is small and the ingest is trivially reversible if terms turn out to prohibit it. |
| 14 | **Satellite imagery licensing.** ArcGIS World Imagery restricts bulk caching | **Resolved — Appendix A.** Not needed as a model feature; stays live-and-unpersisted as cartographic context. No engineering, no licensing exposure. |

### Non-goals

- **No multi-org / RBAC / approval workflow.** Solo dev. The `review_state` columns on `data_source` get filled in by the same person who wrote the plan file, and that is fine.
- **No new ceremony on the operational lane.** Hourly ingests do not get release-set freezes, manifest checksums, or checksummed receipts.
- **No database-enforced governance.** The enforcement layer is being deleted, not extended (Phase 2). Checksums stay as *records*; the CLI owns preconditions.
- **No warehouse rebuild.** `agri` is the warehouse; D1 adds one dimension to `geo`, not a new warehouse.
- **No second geometry table.** `geo.geometry` is the only place a canonical geometry is defined. `agri.spatial_cell` retires; `cell_source_crosswalk` is reused, not re-invented.
- **No new raster catalog table.** `agri.artifact` is it.
- **No request-time forecasting.**
- **No horizon buttons.** The slider is the horizon control (D2).
- **No Type-2 / temporal geometry dimension** in this plan. See risk 7 and open question 4.
- **No animation or interpolation between slider days.** Discrete days only.
- **No forecast-comparison UI, model registry, or skill dashboard.** CLI + existing evaluation tables.
- **No ERA5-Land.** It remains a `credential_gated` declared gap (`covariate_declared_gap.sql`) until CDS credentials exist.
- **No Sentinel-2 pipeline now** — see Appendix A for the trigger.

---

## 9. Estimated effort

A "session" = one focused multi-hour working block. Ranges reflect genuine uncertainty; the wide ones are wide for stated reasons.

| Phase | Sessions | Confidence | What drives the spread |
|---|---|---|---|
| 1 — Port six jobs, swap cron | **4-7** | Medium | Trap-list fidelity (§3). The USDM job alone is ~1 session (geometry repair + prune + release-walk). `ingest/identity.py` is ~0.5 and pays for itself in Phase 3. If the golden-file ID test fails repeatedly, add 2. |
| 2 — Cut enforcement + eval-only lock | **1-2** | **High** | Almost entirely deletion, against 0 rows. The audit measured the drop set exactly (`checksum-layer-audit-2026-08-03.md` §5.3). The only genuine work is deciding the ML lane's fate (open question 3) and re-pointing the two views that read the generated columns. |
| 3 — Geometry dimension + backfill | **3-5** | Medium | The Drizzle table is trivial. The backfill is a single transaction over ~15 k rows that all carry a natural key, so the mechanics are easy; the work is the namespacing scheme and rehearsing it on a snapshot. Repointing three `agri` FKs is mechanical at 0 rows. Add 1 if the rehearsal turns up a collision. |
| 4 — Operational provenance | **2-3** | High | Mostly mechanical: one `publish_operational_release` helper reused six times. `source_release`/`artifact` writers already exist in `execution/source_ingestion.py`. |
| 5 — Five new sources in-house | **7-12** | **Low** | Raster tooling (GDAL/rio-tiler/PMTiles) is the unknown. Terrain is easy (tiles exist, copy a bbox). NLCD/LANDFIRE need reprojection + categorical tiling. Soil is 6 COGs. NDVI needs a scheduled WMTS harvester. MTBS is the easiest (**1-2**: paged vector fetch, no raster toolchain). Budget 1.5-2.5 per raster source. |
| 6 — Narrow fact + covariates v2 + MC + slider | **7-11** | Medium | `covariate_daily_features` is a large, carefully-tested SQL function; extending it is delicate and its contract test (from `0011`, extended by `0016`) must be extended too (~3). The slider is real front-end work, not a control swap (~3): store, component, capability gating, band rendering, date-threaded reads at 6 call sites, cache-key strategy. Projection CLIs ~2. |
| 7 — ML variant on the toggle | **4-8** | **Low** | Depends on whether a trainable target exists, and on open question 3's answer — rebuilding a narrow ML lane costs 1-2 more than reusing the matview. Known blocker: the `0013` strategy-selection plane has zero labelled rows, so the *existing* ML target is not trainable; a different target may need defining first, which is not scoped here. |
| **Total** | **28-48** | | Phases 1-4 (**10-17 sessions**) deliver reliability, a simplified schema, single-source identity and full provenance before any forecast work starts. Phase 2 is the cheapest session in the plan and the only one with an expiry date. |

---

## Appendix A — Satellite imagery: ArcGIS today, Sentinel-2 later

Owner's steer: *"Let's skip switching away from ArcGIS if we can get away with not using it for forecasts. If we need it for forecasts, let's persist it, and look at what the timeline for the open version would look like and what the trade-offs would be."*

### A1. Two different ArcGIS things — do not conflate them

| Host | What it is | Used for | Kind |
|---|---|---|---|
| `server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` | Esri's **rendered RGB raster basemap**, attributed "Esri, Maxar, Earthstar Geographics" | the `satellite` map style (`src/lib/map/sources.ts:34`, `src/lib/map/styles.ts:278`) | **cartographic context** |
| `services3.arcgis.com/T4QMspbfLg3qTGWY/...` | Esri-**hosted** ArcGIS Online *feature services* publishing **federal data** — NIFC WFIGS perimeters (`wfigs-fire-perimeters.ts:29`) and MTBS burn severity (`mtbs.ts:22`) | ingestion jobs | **measurement** |

Same vendor domain family, completely different content and completely different licensing. The World Imagery caching restriction has nothing to do with the WFIGS feature service.

### A2. Is World Imagery needed as a model feature? **No.**

The owner's prior is correct, and for a stronger reason than "it's just pictures":

1. **No NIR band.** World Imagery serves 8-bit sRGB PNG/JPEG tiles. NDVI = (NIR − Red)/(NIR + Red); NDWI needs green + NIR; NBR needs NIR + SWIR. None of these bands exist in a rendered tile. There is no arithmetic that recovers them.
2. **Not calibrated.** Tiles are colour-balanced, contrast-stretched and mosaicked across scenes from many dates, sensors and vendors. Pixel values are display values, not radiance or reflectance. Two adjacent pixels can come from different satellites in different years.
3. **The killer, in this codebase's own terms: no per-pixel acquisition date.** A mosaic has no single `observed_at` and therefore no honest `data_available_at`. It **cannot be written into `agri.signal_observation` without lying about assignment time**, which is exactly what §5's leakage gate exists to prevent. A source that cannot carry an availability timestamp cannot be a model feature here, full stop.

### A3. Decision: ArcGIS World Imagery stays live, unpersisted, cartographic

The repo already encodes precisely this distinction, and I agree with it:

> "The two standing exceptions in that allowlist (ArcGIS World Imagery, AWS terrain tiles) are cartographic context, not measurements — NDVI is a measurement, so it takes the API seam instead of a new exception."
> — `src/lib/server/AGENTS.md:181-184`

The exceptions themselves live at `scripts/check-client-provider-urls.mjs:28-31` and are enforced by `npm run check:data-boundary`, which runs inside the Docker build (`Dockerfile:64-69`).

**"Persist everything we present" applies to data. A basemap is the paper the data is drawn on, not the data.** The owner's rule and the existing gate agree. Because we never cache the tiles, the bulk-caching licensing question never arises — there is nothing to license. No engineering work, no legal exposure, no decision pending.

One caveat, stated plainly so it is a choice and not an oversight: this means the satellite basemap is a live third-party dependency. If Esri changes terms or rate-limits, the satellite style degrades to the vector basemap. That is an acceptable failure mode for cartographic context and would be unacceptable for a measurement — which is exactly why the distinction is drawn where it is.

### A4. Is any ArcGIS-hosted layer a measurement? Yes — and it is already handled

**WFIGS fire perimeters** come from `services3.arcgis.com` (`wfigs-fire-perimeters.ts:29`) and are unambiguously a measurement. They are **already persisted**: `runFirePerimetersIngestionJob` (`ingestion-jobs.ts:313-360`) writes them to `geo.features` (110 rows live). Nothing to do — Phase 1 ports it to Python, Phase 4 adds its `source_release`.

**MTBS burn severity** (`mtbs.ts:22`) is the same shape and was **not** persisted — a live request-time third-party call for a measurement, and a genuine gap in the "persist everything" rule. **D3 closed it: MTBS is now in scope**, specified in §4 and landing in Phase 5. The licensing question is deliberately deferred, not resolved (open question 5).

This sharpens A1's distinction rather than blurring it: both `services3.arcgis.com` layers — WFIGS and MTBS — are **measurements** and are now both persisted. `server.arcgisonline.com` World Imagery remains **cartographic context** and stays live and unpersisted. The host family is the same; the treatment follows the content, which is the whole point of the split.

### A5. The open-imagery (Sentinel-2) option

**What it unlocks that nothing else can:**

Sentinel-2 L2A carries true multispectral surface reflectance — B3 green, B4 red, B8 NIR at 10 m, B11/B12 SWIR at 20 m. That means **we compute the indices ourselves**, at our own cadence and resolution, instead of consuming whatever GIBS chooses to publish. Concretely:

| | Today (GIBS) | With Sentinel-2 |
|---|---|---|
| NDVI | `MODIS_Terra_NDVI_8Day`, **250 m**, 8-day composite (`src/lib/vegetation.ts:24`) | **10 m**, per-revisit (~5 d) |
| NDWI | **does not exist** — GIBS publishes no NDWI product. The NDWI helpers in `src/lib/vegetation.ts` currently have no data source at all. | computed directly (green/NIR, or NIR/SWIR for moisture) |
| NBR / dNBR (burn severity) | not available | computed directly, at our own cadence — MTBS (D3, Phase 5) is annual and ~18 months in arrears, so this is the only route to same-season severity |
| Field-scale resolution | 250 m ≈ 6 ha/pixel — too coarse for a parcel | 10 m ≈ 0.01 ha/pixel |

**The NDWI gap is live and citable today**, and it is the strongest single argument for eventually doing this.

**Realistic timeline, staged:**

| Stage | Sessions | Notes |
|---|---|---|
| Access & auth | 1-2 | AWS Open Data (`s3://sentinel-cogs`, requester-pays-free, no auth, COG-native) is much easier than Copernicus Data Space OAuth. Prefer AWS. |
| Scene selection / STAC query over PNW bbox | 1-2 | ~90 MGRS tiles cover 857 000 km²; STAC search by bbox + date + cloud%. |
| Cloud masking | **2-4** | SCL band classification; the quality of this determines the quality of everything downstream. The genuinely hard stage. |
| Mosaicking / compositing | 2-3 | median composite over a window to fill cloud gaps; reprojection to 3857. |
| Index computation (NDVI/NDWI/NBR) | 1 | trivial once bands are clean. |
| Tiling → PMTiles + R2 upload | 1-2 | reuses the Phase 5 raster toolchain. |
| Cell aggregation → `signal_observation` | 1 | reuses Phase 6 wiring. |
| **Total** | **9-15 sessions** | ≈ 1.5× the entire Phase 5. |

**Storage & compute at the PNW bbox:**

| | Figure |
|---|---|
| Raw 10 m bands, one date, one band | 8.6 × 10¹⁰ px × 2 B ≈ **171 GB** — never store this |
| Computed index raster, int8, 10 m | ~86 GB/date — still not storable |
| Index as PMTiles pyramid, z≤12 | **~0.5-2 GB/date** |
| Rolling 12 dates (~2 months at 5-day revisit) | **~10-25 GB** ≈ $0.15-0.40/mo on R2 |
| Per-cell scalars in `signal_observation` | negligible, keep forever |
| **Compute** | ~90 MGRS tiles/revisit: download + mask + composite is **hours of local CPU per date** |

Retention: rolling 12 composite dates of rasters; scalars forever. Same shape as the NDVI policy in §4.

**Storage is not the problem. Compute and babysitting are.** ~$0.40/month vs. hours of laptop time per refresh.

**Trade-offs:**

| | Keep consuming GIBS | Build Sentinel-2 |
|---|---|---|
| Effort | 0 (already built) | 9-15 sessions + ongoing |
| Resolution | 250 m | 10 m |
| Cadence | 8-day composite | ~5-day revisit (cloud-dependent) |
| Indices | NDVI only | NDVI + NDWI + NBR + anything else |
| Cloud handling | done for us | **ours to get right** — the main failure mode |
| Revisit reliability | composite hides gaps | PNW winter cloud cover can blank whole months |
| Processing-baseline drift | absorbed by NASA | **ours** — ESA baseline changes shift reflectance values and silently break a time series |
| Sensor continuity risk | **high** — Terra is well past design life; MODIS NDVI continuity is a real medium-term risk | low — Sentinel-2 has a funded successor programme |
| Ops burden | none | real and recurring |

**Recommendation — do it when, not whether.**

**Not now.** Nothing in Phases 1-7 needs it: the forecast targets are meteorological and hydrological, and `agri_covariates_v2` gets vegetation from GIBS NDVI at 250 m, which is appropriate for cell-level forecasting.

Build it when **any one** of these fires:

1. **A forecast target needs a moisture index.** NDWI has no GIBS product. If soil-moisture or irrigation-stress forecasting enters scope, this becomes the only path and jumps to the top of the backlog.
2. **A user-facing question needs sub-250 m vegetation** — parcel-level or field-level analysis. 250 m cannot answer it.
3. **MODIS Terra is decommissioned or its NDVI product is retired.** Then it is not an upgrade, it is a migration, and it should be started *before* the gap opens.
4. **MTBS's annual, ~18-months-in-arrears cadence proves too slow** once it is persisted (D3, Phase 5) — dNBR from our own pre/post pairs would then be the deliverable. Persisting MTBS is what will make this concrete rather than hypothetical.

Until one of those fires, **keep consuming GIBS and spend the 9-15 sessions on Phases 1-4**, which have a much better value-per-session ratio.

---

## Open questions

These are genuinely unknown and should be resolved before the phase that depends on them, not guessed at now.

**Closed by D1-D4 and removed from this list:** which `spatial_cell` grid the sources key to (D1 — there is one dimension and `cell_source_crosswalk` does the resampling); which metric goes on the map (D2 — the control is a date, and the fact table is metric-generic); whether Phase 7's ML forecasts the same metric as Monte Carlo (D2 — yes, they are two variants behind one toggle on one slider, so like-for-like is structural); whether `forecast_series` needs a row per variant (D2 — `variant` is a column on `geo.metric_daily`, so the serving read does not care).

1. **What defines the first grid, and who mints it?** D1 made grid cells ordinary geometry rows, but `agri.spatial_cell` has **0 rows and no grids defined**, so `geo.geometry` will have zero `grid_cell` rows after the Phase 3 backfill — every backfilled geometry is a point or a perimeter polygon. A drought or vegetation forecast needs areal cells. Resolution, extent (PNW bbox), and generator (PostGIS `ST_SquareGrid`? H3? align to a source's native grid?) are all unpinned. **Blocks Phase 6.** This is not the old "which existing grid" question — there is nothing to choose between; one has to be created.
2. **Does the slider layer need date-parameterised MVT, or is GeoJSON-over-tRPC enough?** §2.1 recommends GeoJSON because the four declared Martin function sources take `(z,x,y)` only (`infra/martin/martin.yaml:29-40`). Confirm the cell count at full-bbox zoom stays in the low thousands; if a grid choice in question 1 pushes it to tens of thousands, a `(z,x,y,query)` Martin function becomes necessary and that is a different piece of work. **Blocks Phase 6's front end.**
3. **Does "cut the receipt/publication planes" also cut the ML lane, or do those tables survive as plain storage?** Two options, both viable, and the choice must be made **inside Phase 2**:
   - **(a) Keep the tables, cut only the enforcement.** `mv_forecast_ml_daily_serving` and `v_forecast_series_serving` keep working unchanged. This is what the audit's Option 4 actually describes. Cost: ~8 tables of unused schema carried forward.
   - **(b) Cut them and rebuild the ML lane narrow** — an ML writer that emits `(geometry_id, metric_name, valid_on, issued_on, p10, p50, p90)` straight into `geo.metric_daily`, skipping the publication chain. Cheaper to reason about, and **nothing in `src/` writes `forecast_receipt` or `forecast_publication` today** (`models/forecasting.py:852,903,941` are class definitions; `routes/forecasts.py` is undeployed), so no working code is lost. Costs 1-2 extra sessions in Phase 7.
   **Recommendation: (b)**, because the lane is not wired end-to-end anyway and the narrow shape is what the slider needs. But it is the owner's call, and doing it after data lands is much more expensive.
4. **Type-1 or Type-2 geometry for redrawn perimeters?** The dimension as specified overwrites `geom` in place, so scrubbing the slider to last week shows today's WFIGS perimeter shape (risk 7). Options: accept it and mark those layers `temporal_kind='event'`; or add `valid_from`/`valid_to` to `geo.geometry` and make the fact join time-aware. The second is a materially bigger design and would change every read in §2.1. **Decide before Phase 6's slider ships**, not before Phase 3.
5. **MTBS licensing under Esri AGOL hosting terms** (D3, deferred by the owner). The underlying USGS/USFS product is normally public domain; the AGOL feature service's terms are a separate instrument. Determine before publishing derived tiles to a public CDN, which is a later and larger exposure than persisting rows privately.
6. **Where does the per-layer capability flag live?** §6 proposes three columns on `geo.layers`. The alternative is a front-end config constant, which is cheaper but drifts from what the data actually supports. Columns are recommended; confirm before Phase 6 writes the Drizzle migration.
7. **Whose "today" defines the observed/forecast boundary?** Server date in UTC, or the viewer's local date? They disagree for up to a day, and the slider's hatched region, the toggle's enabled state and the `WHERE valid_on = $date` clause must all agree on one answer. Pin it to a single server-supplied `current_date` returned with the layer capabilities.
8. **Exact GIBS publication latency** for `MODIS_Terra_NDVI_8Day`. Needed for an honest `data_available_at`. Measure it empirically over a few weeks rather than assuming.
9. **R2 bucket layout and lifecycle rules.** `scripts/deploy-pmtiles.sh` syncs `data/pmtiles/*.pmtiles` to the bucket root. The 24-month NDVI prune needs a prefix convention (`ndvi/<date>.pmtiles`) and either an R2 lifecycle rule or a prune command. Decide before Phase 5 uploads anything.
10. **Where does the cron container get its DB URL?** The Python service reads a loader URL (`require_local_source_loader_database_url`); the Railway cron service will need the private-network `plantgeo` URL wired as a reference variable. Mechanical, but confirm before Phase 1 cutover.
