---
type: runbook
---

# PlantGeo — Runbook

**Last updated:** 2026-08-20. **READ §0 THEN §0.3-§0.10.** Client batch **shipped as
`de3139e`**; agri batch **blocked NO-GO**; **new memory evidence in §12.6** (idle ~50 MB, one query → 1 GB);
**dropping TimescaleDB is now AUTHORIZED but is measured NOT to be the cause — §12.7.** · **Branch:** `main` (level with `origin/main`) · **Last commit:** `e71e1cd fix(agri): pin the one parameter two lanes read twice, and the pin that went stale under it` (2026-08-18) · **Working tree is CLEAN — the batch this line used to call uncommitted shipped as `160388f`, `44c2133`, `5354df7`, `e71e1cd`.** **READ §0 FIRST: the memory cap is 2 GB, not 3 GB, and `geo.mv_signal_cell_daily` has been DROPPED — much of what follows assumes otherwise.** **§0.3-§0.10 (2026-08-20) add the ingestion write path, the application read path, the Parquet bucket, and the deprecation plan — and §0.4 records a BLOCKER (runtime layer creation ⇒ a DEFAULT partition is mandatory) that invalidates §0.1 step 1 as written.** **A PRODUCTION CHANGE WAS APPLIED 2026-08-17** — `TILE_CORS_ORIGIN` on `plantgeo-martin` (§2). Earlier "root-cause outage found and FIXED" framing stands; §2 and §9 remain the evidence.

---

## 0. HANDOFF — 2026-08-18. START HERE.

**Supersedes the 3 GB memory-cap premise that every section below this one assumes.**

### Goal

Get PlantGeo's database under its Railway memory cap without a materialized view, a continuous aggregate, or
TimescaleDB anywhere in the design. Owner's words: *"the main goal is to make it so that no materialized views or
continuous aggregates + timescale db memory hogging features are needed."* The engine-migration path was
investigated exhaustively and refuted — see `docs/research/timescale-pivot-2026-08-17/report.md`.

### THE CAP IS 2 GB. IT WAS 1 GB THIS MORNING. NOTHING BELOW THIS SECTION KNOWS THAT.

Every prior document — this runbook's §5, the memory notes, the whole research corpus — reasons about a **3 GB**
cap. That was never re-verified. Measured and confirmed by the owner 2026-08-18:

- The cap had been set to **1 GB**, which is **below the measured working set of a single query** (one census sort
  measured ~1.4 GiB). At 1 GB those statements cannot succeed at any speed — they spill until they time out. The
  Railway graph showed memory pinned flat at the ceiling with CPU near idle: a box waiting on I/O, not working.
- **Uncapped it reached ~50 GB.** That is page cache, not working set — Postgres fills whatever it is given from a
  43 GB data dir and Railway bills it via cgroup. This is the same mechanism §5's "the 3 GB reading is page cache"
  finding describes, at a larger number.
- **Owner raised it to 2 GB on 2026-08-18 as an explicit bridge, not a permanent setting.** The plan is to do the
  structural work until no statement's working set exceeds 1 GB, then come back down.

`effective_cache_size` is 2 GB, which was a 2× lie at a 1 GB cap and is now merely optimistic (100% of container;
~1.25 GB would be textbook). **Deliberately left alone** — do not "fix" it without measuring, changing planner cost
parameters on a degraded box is how you get a surprise.

### State

**Applied to production and verified:**

| change | evidence |
|---|---|
| `autovacuum_max_workers` 10 → 3 | live, `context=sighup`, no restart. Was a **1.28 GB** worst-case floor against a **1 GB** cap — it exceeded the whole container. Confirmed still 3 on 2026-08-18. |
| `hypopg` 1.4.3 + `pg_buffercache` installed | `CREATE EXTENSION` succeeded; `ALTER SYSTEM` **is** permitted on Railway managed PG |
| alembic `20260817_0025` | hand-applied; `alembic_version` verified |
| **`geo.mv_signal_cell_daily` DROPPED** | 2026-08-18. **Database 43 GB → 37 GB.** Was 6,349 MB (heap 3,796 + indexes 2,553), 24,958,092 rows, measured **1,729 s** rebuild. Zero in-database dependents confirmed before dropping. |
| refresh backoff working | ledger shows `consecutive_failures` incrementing (soil_union 3, signal_observation_day 3, soil_survey_grid 2) instead of churning |
| `mv_feature_observation_day` **now succeeds** | 2026-08-18 19:04 — the 300→900 s timeout raise fixed it. It is no longer one of the failing four. |

**Commits on `main`:** `160388f` (backoff/preflight/unshard) · `44c2133` (drizzle 0030-0032, dormant) ·
`5354df7` (runbook) · `e71e1cd` (the 42P08 fix + stale readiness pin).

**Believed-correct but NOT verified:** whether the 2 GB cap survives a Railway-initiated restart; whether
`ALTER SYSTEM autovacuum_max_workers` survives one. Both should be re-checked after the next deploy.

**Broken / degraded right now:**
- The four agent tools that read `mv_signal_cell_daily` — `sql/agent/signal_value_on_day.sql`,
  `signal_neighbors_in_time.sql`, `signals_near_point.sql`, `nearest_signal_cells.sql` — **are degraded by design** until the Parquet path
  exists. Owner accepted this explicitly.
- Three views still fail every attempt, now backing off correctly: `mv_soil_survey_union` (never once succeeded —
  real bug, see below), `mv_signal_observation_day` (300 s timeout), `mv_soil_survey_grid`.
- `mv_feature_observation_day_axis` reports `skipped_missing`, `refreshed=NEVER` — because `drizzle/0031` shipped
  **dormant**.

**Review ledger:** the research corpus had an ACH crucible + contradiction mapping + adversarial personas
(14 agents). The deploy batch `160388f` was reviewed by audit but **shipped a regression anyway** — see below. The
`e71e1cd` fix was self-verified against production with live evidence. **The partitioning work below has no review
verdict yet.**

### Key context — the non-obvious material

**1. A green test sweep is not evidence on this repo unless `AGRI_TEST_DATABASE_URL` was set.** `160388f` shipped
with 3,062 Python + 1,320 JS tests passing and took **both** refresh lanes down in production. The test that catches
it — `tests/test_strategy_mv_refresh_postgresql.py` — **already existed and was silently skipped**. With the gate
set: 3,170 passed. Check the gate before trusting a pass count. Real-DB recipe: local `agri_sweep` on port **5442**.

**2. The bug class, worth internalising: a named parameter used TWICE in one statement.** In
`sql/jobs/upsert_matview_refresh_state.sql`, `:outcome` is read as the column value and again in the
`consecutive_failures` CASE that 0025 added. **SQLAlchemy renders a repeated named parameter as ONE placeholder**,
so PostgreSQL deduced `$6` from both sites: `ERROR 42P08: inconsistent types deduced for parameter $6 — text versus
character varying`. Raised at **parse** time; the statement never ran once. Fix was
`bindparam("outcome", type_=String)` at `jobs/matview_refresh.py:605`. **`check_relations_exist.sql` was innocent —
an early diagnosis blamed it; do not "fix" it.**

**3. Alembic is NOT run by the deploy pipeline.** No `preDeployCommand`, no migration step in
`services/agri-data-service/railway.json` or its Dockerfile. **Hand-apply against `DATABASE_URL_SYNC` before
pushing.** Drizzle migrations *are* automatic on `plantgeo-main` — but the migrator only executes files listed in
`drizzle/meta/_journal.json`, so `0030`-`0032` shipped inert. Registering them is queued work with preconditions.

**4. No index can rescue the census aggregates. Measured, closed.** `hypopg` showed `status = 'published'` matches
**5,029,620 of ~5.03M rows — over 99.9%**. That predicate has no selectivity, so nothing beats a sequential scan of
the 3,723 MB heap. `ix_features_layer_observation_day` **already exists in production** and the planner correctly
rejects it; forcing it is 1.76% *worse*. `drizzle/0031`'s "next lever" note is **CLOSED — the lever does not
exist.** The 166-row fire-perimeters guard was three orders of magnitude too small to matter.

**5. `hypopg` cannot test GiST** (`access method "gist" is not supported`). So `drizzle/0030`'s composite index is
**build-measure-drop**, not preview-then-build. Reversible but not free.

**6. The jsonb→native-column fix is NOT a speed fix.** `drizzle/0031` measured **286,800 ms vs 283,049 ms** —
marginally slower. It buys an **8.5× cut in peak allocation** (33 B/tuple vs 511) and reliability. On this box the
binding constraint is **peak allocation, never latency.** No fix here makes it quick.

**7. Geometry is stored TWICE.** `geo.features` carries an inline `geom` **and** a `geometry_id` FK into
`geo.geometry`, which has its own `geom`. **All 5,029,850 rows carry both.** 5.03M features point at 3,255,832
dimension rows. And the dimension's forward path is not maintained, so the duplicate is not reliably in sync.

**8. `geo.features` has ZERO inbound foreign keys.** Verified. This is what unblocks partitioning — the constraint
that made the hypertable conversion illegal (PK on `id` carrying FKs) does not apply here.

**9. `water-gauges` is 1,392,454 rows over 953 distinct geometries** — a USGS reading log — and is served by **no
tile function**. It owns **27.7%** of `idx_features_geom`, which every tile query's `&&` leg walks. fire-detections
owns 59.9%, also with no tile function. **87.6% of the shared spatial index belongs to two layers that never use
it**; the five style-baked tile layers own 0.21% of the tree they are forced to walk.

**10. `mv_soil_survey_union`'s real bug** is a missing `ST_CollectionExtract(…, 3)` in the `delineation` CTE at
`drizzle/0029_pre_aggregation_layer.sql:918` — the extract runs *after* the unions, too late to stop a MakeValid'd
GeometryCollection reaching `ST_Union`. **Not** an SFCGAL problem; a backend swap is the wrong lever.

### Decisions (2026-08-18, owner)

- **Do not migrate engines.** Materialize/RisingWave have no geometry type; ClickHouse has no spatial index and
  Martin has no ClickHouse support; VictoriaMetrics is float64-only; DuckDB is single-writer; `pg_ivm` is not among
  Railway's 102 available extensions. Full reasoning and citations: `docs/research/timescale-pivot-2026-08-17/`.
- **Raise the cap to 2 GB as a bridge**, not a destination. Come back to 1 GB after the structural work.
- **Drop `mv_signal_cell_daily` now**, rebuild as Parquet later — chosen over export-first because the export
  itself must read 6.3 GB on a constrained box.
- **Execute partitioning, not just spec it.** Owner chose execution knowing this session already shipped one
  regression from a green sweep.
- **DuckDB over Polars** if/when the Parquet path is built — the consumers are SQL files, and DuckDB has real
  spatial. **Skip Iceberg/DuckLake/R2 Data Catalog** — 15+ months in open beta, no GA date.
- **No schema-per-layer.** Partitions give physical separation; schemas add `search_path` surface for no gain.

### Assumptions

- **The 2 GB cap holds and is enough for the partition-copy batches** · default taken: proceed · to reverse: the
  copy of `fire-detections` (3,012,005 rows) is the one batch that could exceed it; chunk it by day if it does.
- **The three degraded agent tools are not user-visible** · default taken: accept the gap · to reverse: rebuild
  `mv_signal_cell_daily` from `drizzle/0029`, ~1,729 s.
- **`geo.features.geom` is authoritative and the dimension is the stale copy** · default taken: partition on the
  inline `geom` and leave the dimension alone · to reverse: cheap now, expensive after partitioning.
- **~1 concurrent user** · default taken: treat this as a single-query working-set problem, not a concurrency one ·
  **never actually measured** — Railway `http_requests` on `plantgeo-martin`/`plantgeo-main` would settle it.

### Relevant files

- `docs/research/timescale-pivot-2026-08-17/report.md` — the full verdict, 7 sections
- `docs/research/timescale-pivot-2026-08-17/FACTS.md` — **669+ lines of measured ground truth and 9 corrections.**
  Later corrections reverse earlier claims in the same file; the later one always wins.
- `services/agri-data-service/src/agri_data_service/jobs/matview_refresh.py:605` — the `bindparam` fix; `:256`,
  `:267-455` the twelve specs and their staleness ceilings
- `drizzle/0029_pre_aggregation_layer.sql:918` — the soil-union `ST_CollectionExtract` bug
- `drizzle/0030_features_layer_geom_tile_index.sql:5-13` — the 45.6 s BitmapAnd EXPLAIN, and `:32-40` the sound
  rejection of per-layer partial GiST indexes
- `drizzle/0031_observation_day_axis.sql:18-51` — the not-faster measurement; its "next lever" note is now CLOSED
- `docs/research/timescale-pivot-2026-08-17/evidence/hypopg-covering-index-2026-08-17.md` — the plans that closed it

### Environment

- Branch `main`, level with origin at `e71e1cd`. Working tree clean except untracked `docs/research/`.
- Prod: PostgreSQL **18.4**, PostGIS 3.6.4, TimescaleDB 2.29.0 (idle, drop authorized). Database **37 GB** after
  the drop. Railway project **Aevani** `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, env
  `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Railway MCP authenticated as owner.
- Prod DSN: `DATABASE_URL_SYNC` in `services/agri-data-service/.env` (gitignored). **Values live there only.**
  Query with `services/agri-data-service/.venv/Scripts/python.exe` — **psycopg2, not psycopg3**: `conn.cursor()`,
  never `conn.execute()`. `SET statement_timeout` on every statement.
- Real-DB tests: gate is `AGRI_TEST_DATABASE_URL`; local `agri_sweep` on port **5442**.
- Installed and unused on prod, all free to try: `h3`, `h3_postgis`, `pg_repack`, `hll`, `roaringbitmap`,
  `postgis_sfcgal`. `pg_stat_statements`/`pg_qualstats`/`pg_stat_kcache` need the **same restart** as the
  TimescaleDB drop — bundle them.

### 0.1 Continuation plan

1. **Execute the `geo.features` LIST partitioning on `layer_id`.** Owner-approved for execution. `geo.features` is
   7,703 MB / 5,025,009 rows across 11 layers sharing one heap, one 311 MB GiST index over **5,028,934** entries,
   one 1,467 MB jsonb TOAST relation, and an **819 MB** jsonb expression index
   (`features_layer_external_id_unique`, larger than the PK and 2.6× the spatial index).
   - **Never `ALTER` in place** — 7.7 GB on a 2 GB box is the same trap as `create_hypertable(migrate_data=>true)`.
     Create new partitioned table → copy **per layer** → swap. Per-layer batching is natural: fire-perimeters
     (166), burn-severity (541), evacuation-zones (643), watersheds (9,396) are instant; only fire-detections
     (3,012,005) and water-gauges (1,392,454) are real batches.
   - PK becomes `(id, layer_id)` — free, because there are zero inbound FKs.
   - **Remodel `water-gauges` during the copy** — 1,392,454 rows over 953 geometries. This alone removes 27.7% of
     the GiST tree.
   - **Fix partition pruning:** SIX Martin-registered tile functions restrict on `l.name` through a join (§0.6 — five of them byte-identical),
     and per `0030:32-40` equivalence classes propagate `f.layer_id = l.id` but never the restriction on `l.name`.
     Resolve `name → layer_id` into a constant first so pruning happens at plan time.
   - **Add a real-DB test with `AGRI_TEST_DATABASE_URL` set before shipping.** This is the specific lesson of
     2026-08-17; do not repeat it.
2. **Fix `mv_soil_survey_union`** — add `ST_CollectionExtract(…, 3)` in the `delineation` CTE, new migration. It has
   never once produced a row.
3. **Register `drizzle/0030`-`0032` in `_journal.json`**, in dependency order, honouring each file's hand-applied
   precondition. `0031` is why `mv_feature_observation_day_axis` reports `skipped_missing`.
4. **Build the Parquet path** for the dropped rollup and repoint the three agent SQL files. Hive-partition by
   **month** (52 files, not 1,560). Spatially sort before writing if geometry ever moves — Parquet has no spatial
   index, only bbox row-group pruning.
5. **Convert the eleven small matviews to incrementally-written tables** — `DELETE WHERE day = :day` + `INSERT`,
   watermark last, one transaction. **Not `ON CONFLICT DO UPDATE`** — upsert cannot delete, so a backfill that
   retracts an observation silently orphans rows.
6. **Drop TimescaleDB + `timescaledb_toolkit`** last, so each earlier change's relief stays measurable. Needs
   `tracking.positions` un-hypertabled, a `shared_preload_libraries` edit, and a restart — bundle the three
   diagnostic extensions into that same restart.
7. **Then lower the cap back toward 1 GB** and confirm no statement's working set exceeds it.

### 0.2 Open questions

- **Does the 2 GB cap survive a Railway restart?** Trigger: the next deploy. Same for `ALTER SYSTEM`.
- **Is `geo.features.geom` droppable in favour of the geometry dimension?** Trigger: before partitioning locks the
  column layout in. Worth answering *now* — it is 311 MB of GiST plus the column's heap share.
- **Real concurrency.** Trigger: if partitioning does not deliver, this premise is the next thing to doubt.
- **Peak memory during a 1,729 s refresh** — now unmeasurable, the relation is dropped. If a rebuild is ever run,
  capture it.


### 0.3 Measured schema of `geo.features` (prod, 2026-08-20)

Total **7,872 MB** = heap 3,790 + indexes 2,563 + TOAST 1,518. Live per-layer counts, which are the
copy batches:

| layer | rows | layer | rows |
|---|---:|---|---:|
| fire-detections | 3,019,709 | watersheds | 9,396 |
| water-gauges | 1,413,932 | evacuation-zones | 648 |
| soil-survey | 238,986 | burn-severity | 541 |
| vegetation | 185,031 | fire-perimeters | 172 |
| sensors | 180,654 | interventions | 2 (0 published) |
| weather-observations | 31,569 | **total** | **5,080,640** |

Columns: `id` uuid NOT NULL DEFAULT `gen_random_uuid()`; `layer_id` uuid NOT NULL; `properties` jsonb
NOT NULL DEFAULT `'{}'`; `status` varchar(20) DEFAULT `'published'`; `review_note` text; `created_at`
timestamptz DEFAULT `now()`; `updated_at` timestamptz DEFAULT `now()`; `geom` geometry(Geometry,4326);
`geometry_id` uuid; `data_available_at` timestamptz.

**Two OUTBOUND FKs** — §0's "zero inbound FKs" is right but was only half the picture. Both must be
re-created on the new parent: `features_layer_id_layers_id_fk (layer_id) -> geo.layers(id) ON DELETE
CASCADE` and `features_geometry_id_fkey (geometry_id) -> geo.geometry(geometry_id) ON DELETE RESTRICT`.
The CASCADE is partition-aware, so layer deletion needs no change.

**One row trigger the plan never mentioned:** `geo_features_sync_geom` BEFORE INSERT OR UPDATE OF
`properties` FOR EACH ROW -> `geo.sync_feature_geom_from_properties()` (`drizzle/0001_handy_riptide.sql:185-187`,
redefined `drizzle/0004_repair_ingested_geometries.sql:4`, `search_path` pinned
`drizzle/0008_geometry_dimension.sql:113`). BEFORE-row triggers are legal on a partitioned parent and
must be re-created there. The write path is not a plain INSERT.

All 11 indexes, by size — every one must be re-created by **exact name**:
`features_layer_external_id_unique` UNIQUE (layer_id, (properties->>'id')) WHERE (properties ? 'id') **830 MB** ·
`ix_features_layer_geom` GIST (layer_id, geom) WHERE published AND geom NOT NULL **449 MB** ·
`idx_features_geom` GIST (geom) **313 MB** · `ix_features_layer_observation_day` **292 MB** ·
`features_pkey` **211 MB** · `ix_features_geometry_id` **158 MB** · `idx_features_layer_updated_at` **75 MB** ·
`idx_features_layer_created_at` **74 MB** · `idx_features_layer_status` **64 MB** · `idx_features_layer` **61 MB** ·
`ix_features_updated_at` **36 MB**.

`ix_features_layer_geom` **exists in production** even though `drizzle/0030` is unregistered — it was
hand-applied. Do not infer applied-state from `_journal.json`.

**Grants: only `postgres`.** No application-role grants to replicate at swap, and RLS is disabled
(`relrowsecurity=false`). One less swap-day trap than expected — verified, not assumed.

**Four indexes become partly redundant once partitioned**, because `layer_id` is constant within every
partition: `idx_features_layer` (61 MB, pure waste), and the leading `layer_id` column of
`idx_features_layer_status`, `idx_features_layer_created_at`, `idx_features_layer_updated_at` (213 MB
combined). Rebuild those three without the leading column and drop the first outright. Separately, per
§0's note, `fire-detections` and `water-gauges` own 87.6% of `idx_features_geom` and are served by no
tile function — per-partition indexing means simply **not** creating the geom index on those two.

### 0.4 BLOCKER — layers are created at runtime, so a DEFAULT partition is mandatory

**This is the single thing that would have taken production down, and no prior document contains it.**

`layersRouter.create` is a `contributorProcedure` — team-editor, **not** admin-only — and mints a
`geo.layers` row at request time: `const [layer] = await ctx.db.insert(layers).values(input).returning();`
(`src/lib/server/trpc/routers/layers.ts:140-149`, insert at `:147`; gate `assertCanCreateInTeam` at `:143`).
`geo.layers.id` is server-minted (`src/lib/server/db/schema.ts:162-164`) — no allowlist, no enum, no
partition registry. Two live UI surfaces call it (`src/components/panels/LayerUpload.tsx:98,109-113`;
`src/components/tools/DrawingToolbar.tsx:201,220` — **CORRECTED 2026-08-20: the path was wrong (`tools/`,
not `map/`) and slice A has since DELETED that file, so `LayerUpload.tsx` is now the only UI surface. The
blocker is unchanged: `layersRouter.create` is a `contributorProcedure` reachable over tRPC with no UI at
all**), and the new id can immediately receive features —
`layerId: z.string().uuid()` is accepted straight from the client (`src/lib/server/trpc/routers/contributions.ts:7-24`).

Without a DEFAULT partition the first feature write into a contributor-created layer fails with
`no partition of relation "features" found for row`: a 500 on `contributions.submitObservation`, and in
ingestion a per-job hard failure that flips the cron exit code
(`services/agri-data-service/src/agri_data_service/ingest/results.py:86-94`). Loud, not silent — but every run.

Python ingestion never creates a layer; it resolves or raises `MissingIngestionLayerError`
(`.../ingest/writer.py:96-98,164-170`). A second layer-creation helper with zero importers exists at
`src/lib/server/services/layers.ts:19-22` — dead today, same hazard if revived.

**Decision: create `geo.features_default` regardless**, plus a periodic drain into real per-layer
partitions. Prior art for the drain exists in this repo for `agri.job_event` (RANGE/day, with an existing
`job_event_default`): `services/agri-data-service/src/agri_data_service/db/maintenance.py`. Optionally
also create the partition synchronously inside `layersRouter.create`'s transaction — but that needs the
app role to hold `CREATE` on schema `geo`, which is **unverified**. The DEFAULT partition is not optional
either way: without it, every future layer-creating code path is one merge away from the same outage.

### 0.5 Write path — the ingestion moves

`[lane]` = subagent-reported, not re-verified.

| file:line | what it does | what must change |
|---|---|---|
| `src/lib/server/trpc/routers/layers.ts:140-149` | mints a layer with no matching partition | **BLOCKER** — see §0.4 |
| `src/lib/server/db/schema.ts:221` | `id: uuid("id").defaultRandom().primaryKey()` — **single-column PK** | **BLOCKER.** -> `.notNull()` + `primaryKey({ columns: [table.id, table.layerId] })` in the trailing config array (`:240-250`). Idiom already used at `schema.ts:94,102,136,395`. Must land in the same PR as the DDL or the next `db:generate` proposes reverting the live PK. |
| `drizzle/meta/_journal.json` | ends at `idx 29`; `0030`/`0031`/`0032` exist on disk, unregistered; `idx 26` has neither entry nor file | **BLOCKER.** `scripts/migrate.mjs:1-45` enumerates from the journal, not the directory — an unregistered file is skipped and **the deploy still reports success**. Also `src/__tests__/security/readiness-migration-contract.test.ts` pins the **last** journal entry's tag + sha256 against `src/lib/server/db/migration-contract.ts` (currently `0029_pre_aggregation_layer`), so any registration forces a contract bump in the same commit. |
| `contributions.ts:37-45` `publishContribution`; `:48-65` `rejectContribution`; `interventions.ts:380-406` `transitionLifecycleState` | `.update(features).where(eq(features.id, ...))` — no `layer_id` in scope | Add a preceding `SELECT layer_id`, then `eq(features.layerId, ...)` in the WHERE. Otherwise every publish probes all N partitions. |
| `interventions.ts:338-374` `castModerationVote` | selects the whole row at `:353-356` (so `feature.layerId` **is** in scope), updates at `:365-371` on `id` only | Cheapest fix on the list — add `eq(features.layerId, feature.layerId)`. Router already has `resolveInterventionsLayerId` (`:128-143`). |
| `scripts/apply-pre-aggregation.mjs:87-90,98-99,216` | `CREATE INDEX CONCURRENTLY ... ON geo.features ...`, then `ANALYZE geo.features` | **Now known broken post-swap — see §0.7.** Must be rewritten for the parent/child topology. `drizzle/0029:72-89` hard-asserts both indexes exist. Note autovacuum does **not** analyze a partitioned parent, so the explicit `ANALYZE` at `:216` becomes load-bearing, not a tidy-up. |
| `scripts/backfill-geometry.sql:31,199-209`; `scripts/rekey-geometry-to-entity.sql:37,152-158` | `LOCK TABLE geo.features ... IN SHARE MODE` then whole-table UPDATE | `LOCK TABLE` on a partitioned parent recurses to every partition — N+1 locks in one transaction against `max_locks_per_transaction`. Both are already-run one-shots still on disk and runnable. Re-scope or retire. |
| `.../sql/ingest/link_feature_geometry.sql:113-118` | `UPDATE geo.features SET geometry_id = ...` | Confirm a `layer_id` predicate is present; add if not. |

**Zero** `SET layer_id =`, **zero** `DELETE FROM geo.features` in `src/`, and **zero**
`TRUNCATE`/`VACUUM FULL`/`CLUSTER`/`REINDEX`/`ctid` against the table. No cross-partition row MOVE exists
today; if a `layer_id` UPDATE is ever introduced it executes as DELETE+INSERT and re-fires the trigger.

### 0.6 Read path — application querying and partition pruning

The decisive question at every site: does it constrain `layer_id` directly (**prunes**), or join
`geo.layers` and filter on `l.name` (**does not prune at plan time**)? Equivalence classes propagate
`f.layer_id = l.id` but never the restriction on `l.name`.

**Six of the seven Martin-registered tile functions read `geo.features`, and all six fail to prune.**
(§0.1 said five — it undercounts.) Registry verified at `infra/martin/martin.yaml:43-67`, `auto_publish: false`:
`geo.burn_severity_tiles` (`drizzle/0015:63-104`, WHERE `:103`) · `geo.evacuation_zone_tiles` (`0015:116-155`, `:146`) ·
`geo.fire_risk_tiles` (`0015:159-197`, `:185`) · `geo.sensor_tiles` (`0015:198-237`, `:225`) ·
`geo.intervention_tiles` (`drizzle/0005:6-38`, `:32` — predates the observation-day column, so not byte-identical to the 0015 four) ·
`geo.watershed_tiles` **detail branch only** (`drizzle/0023:131-207`, `:178`; coarser branches read `watershed_rollup`).
`geo.building_tiles` reads `osm_buildings` and is unaffected. `geo.strategy_recommendations_tiles` exists
in the DB but is **not registered in martin.yaml** — never served.

`CREATE OR REPLACE FUNCTION` keeps names and signatures so `martin.yaml` needs no edit, but **Martin must
be restarted** and its config is baked in by `Dockerfile.martin`, read once at container start. A missing
or broken tile function 404s the *whole* composite and hides **every** layer.

Highest-frequency application readers, all `l.name`-join, all needing the same mechanical fix:

| file:line | why it matters |
|---|---|
| `src/lib/server/services/environmental-read-model.ts:4068-4100` `getMetricAtDate` | fires on **every date-slider scrub** — highest-frequency reader found |
| `...:1373-1402` + `:1430-1451` `getPublishedVegetationIndex` | deepest layer (4 years, 184,409 rows), so the unpruned fan-out is proportionally worst |
| `...:623-638` `getPublishedWeatherForPoint` | KNN `ORDER BY geom <-> ... LIMIT n` over an unpruned Append needs a MergeAppend of per-partition GiST-KNN scans — extra scrutiny |
| `...:324-347`, `:378-397`, `:458-488`, `:510-525`, `:696-726`, `:787-802` | fire-detections / water-gauges / weather readers |
| `src/lib/server/services/usda-soil.ts:974-1006`, `:1059-1099`, `:1159-1199` | needs a module-level resolved-id cache |
| `src/lib/server/services/regional-context.ts:359-383` | fires on point-click |
| `src/lib/server/trpc/routers/wildfire.ts:113-132`; `visualization.ts:106-131`, `:133-160`, `:162-192` | `visualization.*` take a **client-supplied** `layerName` — resolve via cached lookup before touching features |
| `.../sql/agent/feature_value_near_point.sql:104-125`; `fire_history_near_point.sql:103-126` | `:surface_name` is a **bind parameter** — strictly worse for the planner than a literal |
| `.../sql/execution/{load_observations,insert_spatial_cells,select_candidate_cell_keys,corpus_digest}.sql` | `layer.name = :layer_name` join |
| `.../sql/jobs/matview_refresh_watermark_watershed_features.sql:12-14` | the refresh gate for `watershed_rollup` — every tick pays a full fan-out |
| `scripts/export-ndvi-grid-tiles.py:41-48` | `DISTINCT ON` over an unpruned Append — the worst shape on the list |
| `drizzle/0029:807-865` `mv_soil_survey_grid`; `:910-960` `mv_soil_survey_union`; `drizzle/0023:20-98` `watershed_rollup` | matviews carrying the same defect — do not ship it into a new relation |

**The four reference implementations** — every fix above is a mechanical port of these:
`interventions.ts:209-235` `listMySubmissions`, `:253-294` `listProposed`, `regional-context.ts:428-459`
`readCommunityProposals`, `services/ingest.ts:56-64`. All resolve name -> `layer_id` constant first.

**No-layer-predicate reads** (become cross-partition scans; some are correct by design):
`src/app/api/v1/features/route.ts:41-90` pushes `eq(features.layerId, ...)` **only `if (layerId)`** (`:52`) —
public paginated browse, unscoped `ORDER BY id LIMIT/OFFSET` becomes a cross-partition merge sort; either
require `layer_id` or require bbox + cap. `contributions.ts:82-97` `listPendingReview` is deliberately
all-layer (comment `:78-80`) — cannot prune without changing the product requirement; note prod's
`idx_features_layer_status` leads with `layer_id` and will **not** serve a bare `status` filter, so add a
per-partition partial index `WHERE status <> 'published'` or accept the scan.
`.../sql/jobs/matview_refresh_watermark_features_updated_at{,_hourly}.sql` do `max(updated_at)` with no
layer predicate — today an O(1) backwards walk on `ix_features_updated_at`, post-swap a MergeAppend over N
per-partition indexes. **Re-measure**: this gate runs for *every* `geo.features`-backed matview.

The census matviews (`drizzle/0029:140-306`, `:720-731`, `:757-767`; `drizzle/0031:134-154`) are all-layer
**by design** — nothing to fix. Confirm `enable_partitionwise_aggregate` post-cutover.
`geo.mv_layer_feature_stats` is the cheapest post-swap smoke test: refresh it and diff the 11 counts.

**Readiness probe coupling:** `src/app/api/ready/route.ts:55,60` does `to_regclass('geo.features')` and
`to_regclass('geo.features_layer_external_id_unique')`. If the unique index is not re-created at the parent
under that **exact** name, readiness fails and the deploy is marked unhealthy.

`sql/agent/observation_coverage_on_day.sql` and `observation_temporal_neighbors.sql` do **not** read
`geo.features` (they read `geo.v_observation_day_census`); `src/lib/server/services/analytics.ts` does not
either. Recorded so they are not re-audited.

### 0.7 Resolved unknowns — tested against prod 2026-08-20

| question | verdict |
|---|---|
| Is a **partial** UNIQUE index legal on a partitioned parent? (`features_layer_external_id_unique`, 830 MB — the plan is invalid as written if not) | **YES — VERIFIED.** `CREATE UNIQUE INDEX ... (layer_id, ((properties->>'id'))) WHERE (properties ? 'id')` succeeded on a PG 18.4 LIST-partitioned probe table. Global uniqueness holds because one `layer_id` implies exactly one partition. |
| Is `PRIMARY KEY (id, layer_id)` legal? | **YES — VERIFIED** on the same probe. |
| Does `CREATE INDEX CONCURRENTLY` work on a partitioned parent? (lanes disagreed; `drizzle/0030` comments claim "PG14+ supports this") | **NO — VERIFIED FALSE.** `ERROR: cannot create index on partitioned table "..." concurrently`. **The `0030` comment is wrong and `scripts/apply-pre-aggregation.mjs:87,98` is broken post-swap.** Verified workaround: `CREATE INDEX ON ONLY <parent>` (lands `indisvalid=false`) -> per-partition `CREATE INDEX CONCURRENTLY` -> `ALTER INDEX ... ATTACH PARTITION`; the parent flips valid only once **every** partition index is attached **and valid**. |
| Are there grants or RLS to replicate at swap? | **NO — VERIFIED.** Sole grantee `postgres`; `relrowsecurity=false`. |
| Peak memory of a 1,729 s refresh | Still unmeasurable — relation dropped. |
| Does the app role hold `CREATE` on schema `geo`? | **STILL UNKNOWN** — only needed if synchronous partition creation is chosen over the DEFAULT partition. |
| Does execution-time pruning rescue the `l.name`-join sites? | **NOT DERIVED, deliberately.** Plan-time pruning is what the fix guarantees; execution-time pruning is not a substitute inside a plpgsql tile function whose plan is cached across a long-lived Martin session. `EXPLAIN (ANALYZE, BUFFERS)` post-swap before deprioritising any site. |

**Ingestion is live right now.** `pg_stat_activity` showed an active ingest backend mid-batch during this
session, and it is what made the probe `CREATE INDEX CONCURRENTLY` time out at 30 s. The same contention
will queue the swap's `ACCESS EXCLUSIVE` rename — and a queued `ACCESS EXCLUSIVE` blocks **every reader
behind it while it waits**. Set a short `lock_timeout` on the rename and retry rather than letting it pile up.

### 0.8 Swap window

1. **Quiesce writers** — three Railway crons plus the app. Ingest commits per 100-row batch
   (`writer.py:38,300-306`) and per 200-row repair page (`backfill.py:69`), so a stopped cron leaves no
   partial batch; a running one holds `RowExclusiveLock`.
2. **Copy per layer, verify, then rename.** Per-layer `INSERT ... SELECT` must run **outside** the migrator
   transaction — every drizzle `.sql` runs in one transaction (stated at `drizzle/0031:27-28`), which is
   also why no `CONCURRENTLY` can appear inside one. Follow the established pattern: expensive work
   out-of-band, then ship a migration whose only executed DDL is a `DO $$` precondition assert
   (`drizzle/0030:196-224`, `drizzle/0032:41-51`). Assert per-layer counts match before the rename.
3. **Re-create on the new parent by exact name:** both outbound FKs, the `geo_features_sync_geom`
   BEFORE-row trigger, and all 11 indexes (`features_layer_external_id_unique` and `ix_features_layer_geom`
   especially — the readiness probe names the first).
4. **`ANALYZE` the parent and every partition.** Autovacuum does not analyze a partitioned parent; the
   expression index has no statistics until analyzed, and without them the planner reverts to the
   sequential scan the index exists to replace.
5. **Restart Martin; bounce `plantgeo-main`.** Martin's config is read once at start and a stale plpgsql
   plan against the pre-swap OID would serve from the orphaned heap. The app pool is `max: 20` postgres-js
   with server-side prepared statements (`src/lib/server/db/index.ts:7-9`) — bounce rather than reason
   about plan invalidation across a two-step rename.
6. **Un-quiesce ingestion last and watch the first cron exit code** — a missing partition surfaces there.
   Then refresh `geo.mv_layer_feature_stats` and diff the 11 per-layer counts against the pre-swap census.

### 0.9 Parquet bucket — provisioned 2026-08-20

Railway object-storage bucket **`plantgeo-parquet`**, id `79d5b0c0-059a-40a9-a90a-ef8d15bb5828`, region
**`sjc`** (US West), project Aevani, env production. Created for the rollup that replaces the dropped
`geo.mv_signal_cell_daily`. Nothing is written to it yet.

Still to wire: S3 credentials as service variables on the writer + reader services (prefer Railway
reference variables over copied secrets); the export job (Hive-partition by **month**, 52 files, not 1,560);
and the readiness reporting below.

**Readiness surfaces to update — owner chose BOTH:**
- `services/agri-data-service/scripts/readiness.py` — add a Parquet/bucket freshness check as another
  fault-isolated section (every check there is already fault-isolated; a failed query reports and the rest
  of the report still runs).
- `src/components/panels/JobRunnerDashboard.tsx` — surface Parquet rollup coverage in the existing
  `gaps` tab (tabs are `lanes | history | gaps`, `:70`). This is the admin UI at `/admin/jobs`.

Note `layers.getIngestionCoverage` (`src/lib/server/trpc/routers/layers.ts:247`) is **not** a readiness
surface — it is the spatial `INGEST_BBOX` badge. Don't extend it for this.

### 0.10 Deprecation removal plan

Summary of the cleanup triage: of 27 candidates, **13 delete now**, **9 repoint, not delete**, **5 need an
owner call**, **8 rejected** (two for mis-scoped greps).

**Delete now** — reachability proven zero across import graph, barrel files, dynamic-import path strings,
and App-Router convention: `package.json:25` (`routing:serve`, targets a commented-out compose service) ·
`db_check.ts` · the drawing/annotation/measure cluster (`src/components/tools/{AnnotationLayer,DrawingToolbar,VertexEditor,MeasureTool}.tsx`,
`src/hooks/{useDrawing,useMeasurement}.ts`) · the fleet-tracking cluster
(`src/components/tracking/{FleetPanel,GeofenceEditor,RouteHistory,VehicleMarker}.tsx`) · the imagery cluster
(`src/components/imagery/{SplitView,PanoViewer,StreetCoverage}.tsx` **plus `src/stores/imagery-store.ts`**,
which the original hunt missed) · the SSE live-flash subsystem (`src/components/map/LiveIndicator.tsx`,
`src/hooks/useLiveLayer.ts`, `src/stores/realtime-store.ts`) · stores `drawing-store.ts`, `tracking-store.ts` ·
stranded tests `src/__tests__/stores/drawing-store.test.ts`, `src/__tests__/hooks/use-live-layer.test.ts`.

**Do NOT touch:** `src/components/tools/ServiceAreaDrawTool.tsx` — live, rendered at
`src/components/panels/TeamDetails.tsx:6,410`. `src/hooks/useSSE.ts` — live via `MetricsBar.tsx`.

**Order matters:** `src/components/tracking/GeofenceEditor.tsx:5` imports `@/stores/drawing-store`, coupling
the tracking and drawing clusters. Leaves before stores, tests in the same commit as their subject, then
**one** type-check/lint/test/build sweep at the end.

**Repoint, do not delete** — headed by the `mv_signal_cell_daily` consumers. There are **FOUR**, not the
three §0 names: `sql/agent/{signal_value_on_day,signal_neighbors_in_time,signals_near_point,nearest_signal_cells}.sql`.
`nearest_signal_cells.sql:20,118` reads the identical relation and is guarded identically at
`agent/tools.py:901` — a repoint driven off §0's list would miss it. None of them crash: each is guarded by
a `to_regclass` probe (`tools.py:473`, `matview_refresh.py:640`) returning a typed
`pre_aggregated_plane_unbuilt` refusal. **They are the only surviving specification of the rollup's grain and
column set — deleting them destroys the spec for the Parquet replacement.** Delete them last, after Parquet lands.

**One genuinely broken path, fix independently and first:** `scripts/apply-pre-aggregation.mjs:133` lists
`geo.mv_signal_cell_daily` in `POPULATE_ORDER` and `runPhaseB()` issues `REFRESH MATERIALIZED VIEW` with **no
existence guard**, unlike `matview_refresh.py:736`. On a fresh/DR database phase B refreshes eight real views,
then raises 42P01 and exits 1. Add the same `to_regclass` guard, or drop the entry and fix the "nine matviews"
comment at `:117`.

**`drizzle/0029:533` still creates `geo.mv_signal_cell_daily`** and no DROP is recorded in any migration —
the 2026-08-18 drop was out-of-band. A rebuild from migration history silently resurrects a 6,349 MB /
1,729 s relation. Record the drop in a **new** `drizzle/0033_*`; **never edit `0029`**.

**Most dangerous rejected deletion:** `src/lib/server/trpc/routers/contributions.ts` + `ContributionQueue.tsx`.
The justification that `ModerationPanel` is a functional equivalent is **false** — `contributions.listPendingReview`
(`:82`) queries features across all layers, `interventions.listProposed` (`:253`) is narrower, and
`submitObservation` (`:7`) has no equivalent at all. `conductor/tracks/community_engagement_completion_20260805/plan.md:14`
still carries Phase 1 as **pending**. That is unfinished scope, not dead code.

**Also stale, rewrite rather than delete:** `infra/railway/README.md:12,15,129-312` describes services that no
longer exist and calls the sole live production DB an unproven "replacement candidate"; `:212` cites
`infra/local-warehouse/create-forecast-roles.sql`, deleted in `3fb9acf`. Its Martin CORS guidance (`:96`) and
`## Deployment order` (`:313`) are still accurate.


---

### 0.11 CRITICAL — the seven matviews follow the OID, not the name

**Found 2026-08-20 while writing the swap driver. It is not in any prior version of this plan, and it is
a silent-wrong-answer bug, not a loud one.**

A materialized view's query is stored as a rewrite rule that references the source relation by **OID**.
`ALTER TABLE ... RENAME` does not rewrite that reference. So after the two-step swap
(`features` -> `features_legacy`, `features_new` -> `features`), every matview built on the old heap
keeps reading **`geo.features_legacy`** — successfully, silently, and forever. No error, no missing
relation, just permanently frozen data diverging from the live table.

Confirmed by `pg_depend`/`pg_rewrite` against production; the live dependents are:

`geo.mv_feature_observation_day` (`drizzle/0029:166`) · `geo.mv_layer_feature_stats` (`:728`) ·
`geo.mv_layer_hourly_activity` (`:763`) · `geo.mv_soil_survey_grid` (`:816`) ·
`geo.mv_soil_survey_union` (`:919`) · `geo.watershed_rollup` (`drizzle/0023:36,176`) ·
plus `geo.mv_feature_observation_day_axis` (`drizzle/0031:142`) once 0031 lands — seven in total.

**Each must be dropped and re-created from its migration after the swap.** There is no swap without it,
so the driver cannot refuse on it; `scripts/partition-features.mjs --phase=plan` enumerates the live list
from the catalog and `--phase=swap` prints it as a `!!` block. **This needs an owner decision on
sequencing**, because re-creating them is a full refresh of each — and `mv_soil_survey_union` has never
once produced a row (§0.10), so it will re-create empty and that is expected, not a regression.

Note this also means the post-swap smoke test in §0.8 step 6 is *invalid as written*: refreshing
`geo.mv_layer_feature_stats` and diffing the 11 counts would read the **legacy** heap and match
trivially. Re-create it first, then diff.

### 0.12 What landed 2026-08-20 (code only — nothing applied to production)

Six parallel slices with pre-declared exclusive file ownership; no collisions.

| slice | result |
|---|---|
| **A — deletions** | 21 files deleted + the `routing:serve` script removed from `package.json`. Every basename and exported symbol grepped repo-wide before removal; all surviving hits were documentation, never live code. `ServiceAreaDrawTool.tsx` and `useSSE.ts` verified live and kept. Post-delete dangling-import sweep: zero matches. |
| **B — service reads** | One shared `resolveCachedLayerId()` (module-level cache, caches hits only, never caches a miss — so a runtime-created layer per §0.4 is still found next call) + `clearLayerIdCache()`. Applied at 10 sites in `environmental-read-model.ts`, 3 in `usda-soil.ts`, 1 in `regional-context.ts`. Each resolver miss short-circuits to the exact empty terminal state the old zero-row join produced. |
| **C — routers** | Read-path pruning in `wildfire.ts` + the three `visualization.ts` readers; write-path `layer_id` scoping in `publishContribution`, `rejectContribution`, `castModerationVote`, `transitionLifecycleState`. Kept the `geo.layers` join where `featureVisibilityCondition` genuinely reads `isPublic`/`teamId`, but moved its predicate from `l.name` to the resolved `l.id` — pruning fixed, authorization unchanged. |
| **D — schema + DDL** | `schema.ts` composite PK `(id, layer_id)`. New `scripts/partition-features.mjs` (10 idempotent phases: plan/create/copy/index/trigger/verify/swap/analyze/adopt/rollback) and `docs/pending-migrations/0033-features-partitioning.md`. |
| **E — agri SQL** | `apply-pre-aggregation.mjs` phase-B existence guard (the 42P01 crash) **and** topology-aware index builds using the verified `ON ONLY` -> per-partition CIC -> `ATTACH` path. Pruning fixes in 4 `sql/execution/*`, 1 `sql/jobs/*`, 2 `sql/agent/*`, and `export-ndvi-grid-tiles.py`. |
| **F — tile functions** | `drizzle/0033_tile_function_partition_pruning.sql`, **shipped dormant**. Each of the six functions diffed against its live body: exactly three hunks each, nothing else. Signatures, volatility, `PARALLEL SAFE`, `search_path`, and every predicate preserved. |

**Non-obvious decisions made inside the slices, recorded so they are not re-litigated:**

- **`idx_features_geom` ceases to exist as a name.** A parent index requires a matching child on *every*
  partition, but `fire-detections` and `water-gauges` deliberately get no geom index. So it becomes
  per-partition `features_<slug>_geom_idx` on the other ten. Grep confirms nothing probes that name — but
  stale prose references remain in `src/lib/server/AGENTS.md:1032`,
  `services/agri-data-service/.../agent/AGENTS.md:220`, `agent/tools.py:248`,
  `sql/agent/feature_value_near_point.sql:33`, `fire_history_near_point.sql:23`,
  `tests/test_agent_graph.py:589`. Comments only, no functional break.
- **Index names collide, constraint names do not.** Index names are unique per *schema*, so incoming
  indexes are built with a `_swap` suffix and renamed inside the swap transaction. A failed rename rolls
  the whole transaction back — a failed swap, never a corrupt one.
- **The trigger is created after the copy, not before.** `sync_feature_geom_from_properties()` runs
  `ST_MakeValid` since `drizzle/0004`, so copying with it attached costs a parse per row *and silently
  rewrites* geometries stored before that repair landed.
- **Copy chunking walks `created_at` boundaries via `ORDER BY ... OFFSET n LIMIT 1`** off
  `idx_features_layer_created_at` — an index-only seek with flat memory. `ntile`/`percentile_disc` would
  tuplestore the whole layer, which is exactly what a 2 GB cap cannot afford.
- **Tile functions resolve the layer id at call time, not baked into DDL** — respects `0030`'s explicit
  rejection of environment-specific ids in replayed DDL, and a 12th layer works with no new DDL.
- The three `layer_id`-leading composite indexes (§0.3, ~213 MB of redundancy) are re-created
  **unchanged** for now — narrowing them changes plan shapes on read paths rewritten in this same batch.
  Recorded as a measured follow-up; the index list is a data structure, so it is a one-line change later.
- `water-gauges` is **not** remodelled during the copy (§0.1 proposes it). The copy is byte-for-byte on
  purpose; remodelling during a swap mixes two failure domains.

### 0.13 Gated, not done — read before deploying anything

1. **`drizzle/0033` is NOT registered in `_journal.json`, deliberately.** `0030`-`0032` are already
   dormant and this repo ships migrations dormant on purpose (commit `44c2133`). Registering `0033`
   means the next deploy replaces all six tile functions, and **a single bad tile function 404s the whole
   composite and hides every layer**. Registration is an owner decision with two preconditions: register
   it *and* bump `src/lib/server/db/migration-contract.ts` in the same commit (the readiness test pins the
   last journal entry's tag + sha256), then **restart Martin** and fetch one tile per rewritten source
   **with an `Origin` header** before calling it done. Nothing in the pipeline restarts Martin.
2. **`0033` is claimed by the tile-function migration**, so the migration recording the out-of-band
   `DROP MATERIALIZED VIEW geo.mv_signal_cell_daily` (§0.10) must be **`0034`**. `drizzle/0029:533` still
   creates that relation and no DROP is recorded anywhere; a rebuild from migration history silently
   resurrects a 6,349 MB / 1,729 s view.
3. **The precondition-assert migration for the partitioned table is not written.** It should assert
   `relkind='p'` on `geo.features`, plus `features_layer_external_id_unique` existing and `indisvalid`,
   following `drizzle/0030:196-224`. It was in no slice's scope.
4. **`enable_partitionwise_aggregate` / `_join` are OFF** (§0.7). Without them the censuses gain nothing
   from partitioning. Turn on and re-measure as part of the cutover, not after.
5. **`max_locks_per_transaction` is 128** and the swap touches ~145 relations — index creation must be
   chunked across transactions, and the two ops scripts' `LOCK TABLE geo.features` now takes 13 locks.
6. **Still unpruned, out of every slice's scope:** `usda-soil.ts` `persistCell` (~`:884-926`) joins
   `geo.layers` by name in three places on the **write** path — not in §0.5's table, found during slice B.
   And `scripts/backfill-geometry.sql:31,199-209` + `scripts/rekey-geometry-to-entity.sql:37,152-158`
   still `LOCK TABLE geo.features IN SHARE MODE`.
7. **Not started:** the Parquet export job, the DuckDB repoint of the four agent SQL tools, and the
   readiness wiring in `readiness.py` + `JobRunnerDashboard`'s `gaps` tab (§0.9). The bucket exists and is
   empty; no credentials have been wired to any service yet.


---

### 0.14 Review verdict + sweep evidence, 2026-08-20

**Sweep — GREEN, and gated. This is the number to trust.**

| suite | result |
|---|---|
| Python, `AGRI_TEST_DATABASE_URL` **and** `PGBIN` set | **3,170 passed, 3 skipped**, exit 0 |
| TypeScript `tsc --noEmit` | exit 0 |
| `eslint .` | exit 0 (warnings only, all in the vendored minified `static/datastar.js`) |
| `vitest run` | **1,305 passed, 13 skipped**, exit 0 |
| `next build` | exit 0 |

3,170/3 matches the documented fully-gated baseline exactly; an ungated run is ~3,062. **Two near-misses
worth keeping**, both of which would have produced a fake green:
- The gate looked *unavailable* at first — `agri-sweep-db` binds IPv4-only, so `localhost` resolves to
  `::1` and refuses, and its credentials are container-local, not the ones in CLAUDE.md. See
  [[agri-real-db-testing-gap]].
- A first sweep attempt reported `exit code 0` while running **zero** tests: pytest rejected an
  unrecognised `--timeout` flag and the exit status came from the `tail` at the end of the pipe.
  **Never read an exit code through a pipeline.**

**Adversarial review: `/code-review high`, separate context, CHANGES-REQUIRED — 7 findings.** Six were
dispatched for fix; the seventh is an owner decision recorded below. What the reviewer independently
verified clean is itself useful: no dangling imports to the 19 deleted modules; `layers.name` is UNIQUE,
so every scalar-subquery rewrite is row-for-row equivalent to the join it replaced; no FK and no
`onConflict` targets `features.id`, so the PK change is safe at the ORM layer; `drizzle/0033`'s six tile
functions reproduce the original predicates and MVT attribute lists exactly; and `partition-features.mjs`'s
chunk predicates are disjoint and exhaustive **including** the `created_at IS NULL` bucket.

| # | severity | finding |
|---|---|---|
| 1 | high | `visualization.ts:10` caches a **null miss** for the process lifetime — a runtime-created layer renders permanently empty. Inconsistent with the sibling resolver, which caches hits only. |
| 2 | high | `environmental-read-model.ts:300` `layerIdByName` has **no invalidation**, but `layersRouter.delete` (`layers.ts:178`) falsifies the "an id never changes once minted" premise: delete + recreate under the same name silently empties ~10 readers until restart. |
| 3 | medium | `apply-pre-aggregation.mjs:282` post-swap builds a **second** child index per partition then fails on `ATTACH`, because `partition-features.mjs` already attached them — aborting before `ANALYZE` and leaving duplicates. |
| 4 | medium | `wildfire.ts:136` `getInterventions` is a public read that now **throws** where it previously returned `[]`. |
| 5 | medium | `interventions.ts:394` dropped its post-update NOT_FOUND throw; a delete racing the new pre-flight SELECT resolves to `undefined`. Same shape in `castModerationVote` and both `contributions` mutations. |
| 6 | low | `features/route.ts:52` enforces a 10k offset ceiling while `parsePageValue` still advertises 1,000,000. |

**All six FIXED and re-verified 2026-08-20** — `tsc --noEmit` exit 0, `vitest run src/__tests__` 1,305
passed / 13 skipped. Notes worth keeping:
- **#1 + #2 collapsed into one resolver.** `visualization.ts`'s second cache was deleted outright rather
  than patched, so there is now one resolver, one miss policy, one expiry, one invalidation path. Its
  200-entry bound moved into the shared resolver, which is where every client-supplied name now lands.
- **#2's strategy is TTL-first (60 s), explicit-evict-second, and that ordering is the point.** An
  explicit evict alone cannot be correct here: the cache is per-process, so a delete handled by one Node
  process leaves every other process stale until restart — and `geo.layers` rows also vanish out-of-band
  via `features_layer_id_layers_id_fk ON DELETE CASCADE`, psql, and the ops scripts, none of which pass
  through any call site. The TTL heals all of those with no call site at all. The evict is kept because
  it makes the common in-process delete heal instantly instead of serving up to 60 s of empty reads.
- **A rename was found that the review did not name.** `layersRouter.update` (`layers.ts:151-172`) can
  rename a layer (`layerUpdateSchema` is `layerCreateSchema.partial()`), stranding the OLD name in the
  cache pointing at a live id. The old name is not in scope there. TTL covers it; an explicit-evict-only
  design would not have. Both mutation paths are now wired: `delete` returns the name and calls
  `invalidateLayerId`; `update` calls `clearLayerIdCache()` when `input.name` is present.
- **#3 detects by ATTACHMENT, not by name** — `partition-features.mjs` creates parent indexes with a
  plain `CREATE INDEX`, so PostgreSQL generates and attaches the children itself under names this script
  could never guess. An attached-but-invalid child is reported loudly rather than worked around, because
  an attached child cannot be dropped while its parent requires it.
- **#5 keeps a deliberate asymmetry in `contributions.ts`**: a *pre-flight* miss still returns `undefined`
  (what this router has always answered for a feature that is simply gone), while passing the pre-flight
  and then matching nothing — the actual race — now throws. `ContributionQueue.tsx:76-88` only wires
  `onSuccess`, so throwing on the former would leave a deleted row on screen.

**FINDING 7 — NOT FIXED, NEEDS AN OWNER DECISION.** `schema.ts:244` now declares the composite PK
`(id, layer_id)` while the partition swap is deliberately **not applied**, so `schema.ts` and production
disagree. The hazard is real in both directions and there is no configuration that is safe in both:

- **Leave it composite (current state):** the next `npm run db:generate` emits a `DROP`/`ADD PRIMARY KEY`
  against the live **5.08M-row unpartitioned heap**, inside a single-transaction migration that the deploy
  pipeline runs automatically. On a 2 GB box that is not survivable.
- **Revert it to single-column:** safe today, but the moment the swap runs, `db:generate` proposes
  reverting production's PK back — the failure mode `slice D` warned about.

The coupling is unavoidable: **`schema.ts` must flip in the same commit as the swap.** Until the swap is
scheduled, treat `db:generate` as blocked. `migration-contract.ts` still pins `0029`, which is correct and
intentional — `drizzle/0033` is dormant (§0.13).

**RESOLVED 2026-08-20 — REVERTED to single-column `.primaryKey()`, `tsc --noEmit` exit 0.** The deciding
argument is that the swap is **not imminent**: it is gated behind the `enable_partitionwise_aggregate`
re-measurement (step 4 of the plan, which may refute the premise before 7.7 GB is moved), behind quiescing
three crons, and behind the §0.11 owner decision on matview sequencing. Both hazards are real, but they are
not symmetric in *when* they fire. Composite-while-prod-is-single-column arms a routine, unattended action
— any `db:generate` by any developer or agent emits `DROP`/`ADD PRIMARY KEY` against the live 5.08M-row
heap in an auto-deployed single-transaction migration. Single-column arms nothing today; its hazard fires
only *after* the swap, at the exact moment the runbook already requires `schema.ts` to flip anyway. So the
revert restores the invariant that makes `db:generate` safe — **`schema.ts` describes production** — and
moves the remaining risk onto a step that is already documented as mandatory. The composite PK now lives as
a comment at `schema.ts:242-245` naming the swap as its trigger; `docs/pending-migrations/0033-features-partitioning.md`
is unchanged and still specifies `(id, layer_id)` as the target. `db:generate` is **no longer blocked**.


---

## 1. Goal

Get the 3 GB-capped production database to a small, honest working set — the application runs **no analytical
queries**, every serving path reads a pre-aggregated relation, and base (non-reclaimable) memory is cut so the
Railway cap can come down. Alongside it, make every data layer self-healing per
[`docs/layer-lane-standard.md`](../docs/layer-lane-standard.md) so lanes close their own gaps without an agent
watching.

Done looks like: no analytical query on any request path, base memory measured and the cap right-sized, the
hourly pulse green and cheap, and no fabricated data on any serving path.

---

## 2. State

### Dynamic layers were CORS-blocked in prod — found and FIXED 2026-08-17

**Root cause of every dynamic map layer rendering blank in production: Martin's CORS allow-list named the wrong
origin.** This is the single most important state change of the session, and it dominates §9.

- Prod Martin carried `TILE_CORS_ORIGIN=https://plantgeo-main-production.up.railway.app`. The app's **canonical
  user-facing origin is `https://plantgeo.aevani.com`**. **BOTH domains are ACTIVE** on `plantgeo-main` — custom
  domain `plantgeo.aevani.com` (`03f92270-f8f6-414c-bac8-abeb2fa4c5dd`) and service domain
  `plantgeo-main-production.up.railway.app` (`de80644f-46a3-4530-8906-8faaa03d00f6`), both `target_port` 8080.
- `infra/martin/martin.yaml:7-10` interpolates that single var into `cors.origin` as a **one-element YAML list**:
  `- "${TILE_CORS_ORIGIN:http://localhost:3001}"`.
- Result: Martin answered `HTTP/1.1 200 OK` with
  `vary: accept-encoding, Origin, Access-Control-Request-Method, Access-Control-Request-Headers` and **NO
  `Access-Control-Allow-Origin` header at all** — the CORS layer was active, simply did not match, and emitted
  nothing. `OPTIONS` preflight returned **400 Bad Request**. The browser blocked the response.
- Live console error from prod:
  `MapLibre reported a load error. If the basemap is blank, the pinned Protomaps build may have expired -- set NEXT_PUBLIC_PMTILES_URL to a current archive. eZ: AJAXError: Failed to fetch (0): https://plantgeo-martin-production.up.railway.app/fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles`
- **That error message is actively misleading.** It blames an expired Protomaps/PMTiles pin; the failing URL is the
  **Martin composite**. **PMTiles was verified healthy:** `https://tiles.aevani.com/pnw-2026-08-02.pmtiles` returns
  `206 Partial Content`, `Access-Control-Allow-Origin: *`, `Cache-Control: public, max-age=31536000, immutable`,
  **1,411,574,646 bytes** total. AWS terrain tiles also return `Access-Control-Allow-Origin: *`.
  **Only Martin was broken.**
- Because `src/lib/map/sources.ts:28-32` composes all six function sources into **ONE** MapLibre source, this single
  CORS failure **failed the whole TileJSON and blanked every dynamic layer at once** — exactly the failure mode that
  file warns about.
- **`curl` did NOT reproduce it, because curl sends no `Origin` header. This is why the defect survived every prior
  server-side investigation** (§5 for the required command shape).

**FIX APPLIED TO PRODUCTION THIS SESSION:** `TILE_CORS_ORIGIN` set to `https://plantgeo.aevani.com` on the
`plantgeo-martin` service via Railway (project **Aevani** `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`). **VERIFIED:** Martin now returns
`access-control-allow-origin: https://plantgeo.aevani.com`, the MapLibre load error is gone from the console, and
the map renders — terrain hillshade, coverage boundary and MapLibre attribution all draw. **Confirmed in a real
visible browser against prod.**

**CAVEAT, known consequence:** `cors.origin` receives exactly **one** interpolated entry, so
`https://plantgeo-main-production.up.railway.app` is now the origin that is **BLOCKED**. Anyone loading the app on
the raw Railway domain gets blank dynamic layers. The durable fix is to make `martin.yaml` list **both** origins —
a repo change plus a Martin deploy, **NOT done** (§7). Also stale: `docs/deployment.md:383` and
`infra/railway/README.md:98` both still document the old (now wrong) value, and `.env.example:61` /
`docker-compose.yml:56` default to `http://localhost:3001`.

**PROVENANCE — the wrong value was set deliberately, which is why it never looked suspicious.**
`.mpg/plantgeo-hardening.json:95` records `TILE_CORS_ORIGIN=https://plantgeo-main-production.up.railway.app`
as a *hardening* decision from an earlier session: at that time the Railway service domain was the only
origin, and locking CORS to one exact origin was correct. The custom domain `plantgeo.aevani.com` became
the user-facing origin afterwards and **nothing re-visited the allow-list**. So this was not a typo or a
default left unset — it was a correct decision that silently expired. **The lesson for §7's two-origin
fix: adding a domain to `plantgeo-main` is not complete until Martin's `cors.origin` lists it.** Treat the
allow-list as coupled to the domain set, the same way the migration contract is coupled to the journal.
A repo-wide search confirms `infra/martin/martin.yaml:10` is the **only** runtime consumer of the
variable; every other hit is docs, `docker-compose.yml`, or a stash.

**What this explains, now settled:** the owner's live observation that the canvas "eventually loads, it just takes a
long time" — the basemap/terrain path (PMTiles + AWS, both `ACAO: *`) always worked and was merely slow; the
Martin-backed dynamic layers were **not slow, they were blocked outright**.

### Live in production (verified 2026-08-16)

Prod is at **drizzle 0029**, alembic `20260814_0023`. Repo and prod agree. Deploy `890f430` succeeded across
services; `plantgeo-martin` was redeployed because 0028 replaced a tile function.

| | before | after |
|---|---|---|
| `geo.features` total | 12 GB | **7,219 MB** |
| — index footprint | 6,804 MB | **2,064 MB** |
| slider day axis | scan 4.97M + 46M rows | **31,321 rows** |
| `effective_cache_size` | 768 MB | 2 GB |
| `work_mem` | 4 MB | 16 MB |
| idle-window cache hit | — | **99.88%** |

- **5,392 MB of dead indexes dropped** `CONCURRENTLY`: `idx_features_properties` (4,372 MB / 11 lifetime scans),
  `idx_features_layer_external_id_lookup` (678 MB / 0), `ix_geometry_centroid` (159 MB / 0), `ix_geometry_geom`
  (183 MB / 1).
- **Two indexes built**, both `indisvalid`: `ix_features_layer_observation_day` (406.9s),
  `ix_features_updated_at` (59.3s). The planner **does** use the first — day bounds are now an `Index Cond`.
- **8 of 9 matviews populated.** `geo.mv_soil_survey_union` failed (see §7 step 3).
- **0028 verified honest:** the three strategy matviews hold 0 rows because their sources are empty,
  `causal_benefit_tau` is gone, `effect_utility_score/_lower/_upper` plus `label_release_key` / `source_count` /
  `label_review_tier` are present, and `geo.strategy_recommendations_tiles(z,x,y)` resolves at z4/z9/z13 via the
  empty-tile path. **The rewrite invented nothing.**

### The matview-refresh lane: root cause found and recovered (2026-08-16, later)

- **Root cause of ~24h of `matview-refresh` / `strategy-mv-refresh` dead-lettering: alembic
  `20260816_0024` had never been applied to prod.** It creates `agri.matview_refresh_state`, the ledger the
  whole watermark lane writes through. Applied. **CONFIRMED RECOVERED:** the ledger now holds **11 rows**,
  **6 views refreshed**, and `agri.mv_forecast_ml_daily_serving` came back as `self_healed_unpopulated`.
- **Four still fail, and now fail VISIBLY every pulse** (the intended improvement, not a regression):
  `mv_feature_observation_day` **302s** against its 300s cap · `mv_signal_observation_day` **301s**, same cap ·
  `mv_soil_survey_grid` **66s** · `mv_soil_survey_union` **133s** — the known GEOS `CollectionExtract` bug,
  §7 step 3.
- **`ANALYZE geo.features` + `geo.layers` run.** Stats were 2 days stale: the planner estimated **499,986** rows
  where the truth is **541**. **NECESSARY BUT NOT SUFFICIENT** — the plan is still >300s afterwards, so the
  missing index is genuine and not a statistics artefact.

### The map: not missing data — but the client IS defective (2026-08-16, corrected 2026-08-17)

**This subsection previously read "not a code bug and not missing data". The second half stands; the first half
is WRONG and is corrected here.** The data-extent evidence below is unchanged and still valid — nothing is cut.
But §9 documents **ten client-side code defects** (D1–D10) reached by read-only code trace, three of them
sufficient on their own to make a correctly-populated layer render stale, empty or mislabelled. "Diagnosed as
purely a database/serving problem" was a premature closure.

- Every `geo.*_tiles()` function collapses to a **BitmapAnd whose GiST leg scans ~1,318,892 FIRMS index
  entries to return 137 rows**: **45.6s per tile**. Eight such tiles saturate Martin's 8-connection pool —
  `intervention_tiles`, a **two-row** layer, was observed running **10 minutes**.
- **The data is NOT cut.** Verified extents: sensors **149,466** features spanning
  −124.56..−111.10 / 42.02..48.99 · watersheds **9,396** · burn-severity **541** · fire-detections
  **3,009,567** at exactly −125..−111 / 42..49. `INGEST_BBOX` ≈ (−125, 42, −111, 49).
- **The green diagonal is `ServiceAreaLayer`'s coverage boundary under camera pitch.** It dims outside and
  clips nothing. Do not chase it as a data defect.
- **Martin has NO statement timeout** — `statement_timeout = 0`, source `default`. `postgres` is the **only**
  login role, so a role- or database-level timeout would also hit the matview lane that legitimately needs
  1800s, and a dedicated role collides with the settled "no custom DB roles" decision. **Remedy:** append
  `options=-c statement_timeout=20000` to `DATABASE_URL` on the **`plantgeo-martin` service only**.
  **STILL NOT APPLIED** — but no longer blocked: the Railway MCP is authenticated again as of 2026-08-17 (§5).
  The remaining reason it is unapplied is that nobody has applied it.

### The map UI, measured live against prod (2026-08-17)

The tile path is no longer an EXPLAIN estimate — it was measured end to end against
`https://plantgeo-martin-production.up.railway.app` and `https://plantgeo.aevani.com`. **Full evidence in §9.**

- **`fire_risk_tiles` returns 26,765 bytes after 117.2 s** — the worst work-to-output ratio in the system, and it
  is the `fire-perimeters` layer, not fire-detections.
- **Cold composite tile: 84.4 s at z6.** Warm repeat of a neighbouring tile: 1.6 s — Martin's 128 MB /
  `tile_expiry: 5m` cache works, it just cannot hold a working set this size, so nearly every pan is cold again.
- **Payload is a second, independent problem: 10,751,237 bytes (10.3 MB) in a single vector tile at z5**, ~2–3 MB
  at z6 — and z5/z6 is where the map opens. `sensor_tiles` alone is **2,155,849 bytes**, 149,466 unclustered
  points serialized whole. Even at zero latency this is a client decode and RAM problem.
- **Tile responses carry no `Cache-Control`** — `etag` only, so a returning session re-pays the multi-MB cold cost.
- The 45.6 s BitmapAnd figure above is a plan cost; the 117 s / 84 s figures are cold wall times under real pool
  contention. They are the same defect measured at two points, not two different numbers.
- The app shell is **not** implicated: `/api/ready` 0.43 s, `/api/health` 0.36 s, TTFB 217 ms, load 1697 ms.

### Migration authored, NOT applied

`drizzle/0030_features_layer_geom_tile_index.sql`. The index must be built **out of band** —
`CREATE INDEX CONCURRENTLY` cannot run inside a transaction:

```
CREATE INDEX CONCURRENTLY ix_features_layer_geom ON geo.features USING gist (layer_id, geom)
  WHERE status = 'published' AND geom IS NOT NULL
```

Needs `btree_gist`. Estimated **25–60 min**, **400–600 MB**. Use `lock_timeout = '20min'`, **not** the `'5s'`
hardcoded in `scripts/apply-pre-aggregation.mjs`. Verify **both** `indisvalid` **and** `indisready` after.
**A failed build leaves an INVALID index occupying the name**, so `IF NOT EXISTS` on a retry silently
succeeds while the 45s plan persists — **DROP it first**. Martin restart **not** required (no function
changed). The journal entry and the migration-contract re-pin **must not land** until it is live in prod
(§5, "Migration ordering").

### Review ledger

| phase | checkpoint | findings | verdict |
|---|---|---|---|
| sharding wave (`4a685a1`) | adversarial verifier on per-shard truncation | 6 scenarios, high confidence | **refuted** — 2 died on contact (pre-S2 snapshot; inclusive bounds shipped), 3 stand (dead-code cap, no test coverage, `session_lock` serialization) |
| pre-aggregation build (`4d1345e`) | adversarial matview-equivalence verifier | 11 defects, high confidence | **refuted, then fixed** — day-rule fork, lane/unit gates, two watermark bugs, join-shape changes |
| prod apply (`890f430`) | measured against prod | index used by planner; census 68.7s single vs 95.3s sharded | **confirmed** |
| layer-lane conformance | §13 audit, 21 enumerated | **0 conformant** | **not uniform** — 12-item backlog stands |
| base-memory reduction | — | — | **unreviewed** — not started |
| pulse standing-failure signal (uncommitted) | adversarial review, 2026-08-16 | 2 HIGH + 6 MEDIUM + 1 LOW | **CHANGES-REQUIRED → 2 HIGH + 2 cheap fixed, 5 deferred** (§8) |
| service worker "probably dead" (§11.7) | live prod browser + build artifacts, 2026-08-17 | 1 claimed HIGH | **REFUTED** — SW is `activated` and CONTROLLING, manifest 200; premise was a `public/**` glob missing `src/app/manifest.ts`. Zero code changed. §11.7 rewritten |
| **all 4 client lanes — REMEDIATION ROUND** | re-verified per lane, 2026-08-17 | every finding addressed or defended | **RESOLVED.** Cache: historical revalidation restored + **four** immutability assertions corrected (reviewer found three), quota ceiling now *learned* from a refused write, 304 fiction deleted, payload walk replaced by a metadata store at DB version 2 so a large budget costs O(entries) not O(bytes). Fires: `60 → 4` cached responses (**~290 MB → ~19 MB**), `, f.id` tiebreakers added to both queries so the content ETag stops flapping, `today` threaded through the read model. Slider: focus moved onto an after-commit effect, **plus a second bug the prescribed fix would have missed** — a successful clear disables the trigger in the same commit and a natively `disabled` element cannot take `.focus()`, so it moved to `aria-disabled` (also keeps the control in tab order); real success signal replaces the dead `catch`; own dotted appearance instead of reusing `dense`. Map: `isShowingPreviousDay` split into **`hasLandedForRequestedDate`** + `isShowingPreviousDay` (they are not negations — a failed request is neither), store made **per-publisher** so climate can publish without two writers erasing each other, coverage now **18/27**. Tests: 28/28, 60/60, 166/166 |
| agri refresh lane (uncommitted) | adversarial review, 2026-08-17 | pending | **UNDER REVIEW** — per-spec parallelism, failure backoff, `drizzle/0031` re-grain, two further livelocks. Author's own gates: ruff clean, mypy 2 pre-existing, 153 pytest, declarative parity green, `0031` executed against prod inside a **rolled-back** transaction with the census view's 8-column contract byte-identical |
| map lane D1/D4/D5/D10 (uncommitted) | adversarial review vs installed `maplibre-gl@5.22.0` + `query-core@5.96.2`, 2026-08-17 | 3 HIGH + 4 MED + 5 LOW; **9 claims survived** | **CHANGES-REQUIRED** — **the lane broke the rule it wrote**: `keepPreviousData` was added to the 9 climate signals with **no** drawn-day report, so they retain the previous day's isobands while the caption states the new one — verbatim against its own *"retaining is permitted, misstating is not; do not add one without the other."* Also: **`isPlaceholderData` is false on ERROR**, so a failed fetch records as a landing and the surface names a day the canvas is not drawing; and `placeholderData` sets `status="success"`, so **`isLoading` is permanently false after first success**, killing three panel loading indicators and freezing panel counts to the previous viewport. Plus **"`styledata` fires once per tile" is FALSE** in 5.22.0 and asserted as load-bearing in four places. **Survived: the deepEqual/no-render-loop claim, the `getLayer()` safety proof, no rAF race, unthrottled-is-correct, no memory growth, and the D1 tests genuinely failing if the gate were restored.** Remediation dispatched |
| slider lane synced-days track (uncommitted) | adversarial review, 2026-08-17 | 3 HIGH + 3 MED + 3 LOW; 8 claims survived | **CHANGES-REQUIRED** — **the lane's own test suite is red**: focus is never returned to the trigger after Cancel, because `isConfirming` toggles the root element type so React nulls the ref before the inline `.focus()` runs (the Confirm path has the identical bug, untested). **A failed clear is indistinguishable from success** — `clearLayerSyncedDays` is documented *never throws*, so the `catch` that sets `clearFailed` is dead code. And `.is-pending`'s animation **outranks `:focus-visible` by cascade origin regardless of specificity**, so a pending thumb shows no focus ring. **Survived: the render-cost fear REFUTED** (`floorAxisRunsToBands` bounds output to ~111 bands however alternating the pattern), memoization holds, note-joining sound, three states render distinctly, gating correct, Enter-Enter on *opening* refuted. Remediation dispatched |
| cache lane C2 + sync index (uncommitted) | adversarial review, 2026-08-17 | 1 CRITICAL + 2 HIGH + 3 lower; 4 claims survived | **CHANGES-REQUIRED** — **the historical no-revalidate rule is a strict C1 REGRESSION**: every correction path for a past day is now closed (revalidation hard-returns, TTL 30 d, the `dataRevision` compare is unreachable, and the 512 MB raise removed LRU eviction, which was the accidental healer) — and the false immutability premise is **re-asserted in three places**, incl. a *new* AGENTS.md claim. Plus an **unrecoverable quota wedge** (a failed `setEntry` freezes the running total, so eviction never fires again — iOS Safari, which pre-16.4 has no `storage.estimate` backstop), and the **304 short-circuit is unreachable production code** whose test asserts a `dataRevision` payload **no allowlisted procedure emits** — the same fiction class as the `job_run.status` finding, and it kills the planned C1 `dataRevision` gate. Remediation dispatched |
| fires lane D3/D7/C5 (uncommitted) | adversarial review, 2026-08-17 | 3 HIGH + 1 MED-HIGH + 5 lower | **CHANGES-REQUIRED** — three findings reintroduce the defect they claim to close: `undefined` aliases two meanings so the staleness flag reads false on the live path; the content ETag hashes a **non-deterministic row order** (no tiebreaker, `LIMIT 2000` cuts through ties) trading false-304 for false-200; and `MAX_CACHED_RESPONSES = 60` retains **~240–290 MB** across two hook instances — **the worst RAM regression in the batch, against the owner's stated constraint.** Plus a TOCTOU on `today` straddling the await that ships a live payload under the 48 h historical contract. Remediation dispatched |

### Not started

Sharding revert · `mv_signal_cell_daily` re-grain · `mv_soil_survey_union` DDL fix · base-memory cuts ·
the 12-item conformance backlog · ML Phase B · USDM/ERA5 self-healing lanes · persistence audit · FIRMS cap.

---

## 3. Decisions

- **Revert the sharding.** Measured, not argued: 68.7s single vs 95.3s across 13 shards, with `shared_blks_read`
  a wash (331,152 vs 332,076). Sharding never reduced work; it added ~27s of overhead. The original 81–101s was
  the missing index all along.
- **`geo.mv_signal_cell_daily` gets re-grained coarser**, not kept and not dropped. At 6,349 MB / 24,968,939 rows
  it is only a ~1.8× reduction because its grain is nearly the source grain. Keep the release-winner dedup, lose
  the width.
- **`geo.mv_soil_survey_union` gets its DDL fixed**, not dropped — and `usda-soil.ts` should then actually be
  repointed at it, since today it has no reader.
- **DECISION CHANGE (2026-08-16, later): the approved `autovacuum_max_workers` 10 → 3 cut is ON HOLD.** Stale
  autoanalyze on `geo.features` was half the cause of the planner collapse (499,986 estimated vs 541 actual);
  cutting workers by 70% risks recurrence. **`max_connections` 100 → 25 is also held** pending a real
  concurrent-connection count, because it pulls against Martin's `pool_size`. The original approval
  (2026-08-16 morning: both cuts with a restart, no users; declining `shared_buffers`/`maintenance_work_mem`
  cuts and dropping TimescaleDB) stands as the *starting* position, not the current one.
- **Martin gets a per-service statement timeout, not a role- or database-level one.**
  `options=-c statement_timeout=20000` appended to `DATABASE_URL` on `plantgeo-martin` only. `postgres` is the
  only login role, so anything broader would also cap the matview lane that needs 1800s, and a dedicated role
  collides with the settled "no custom DB roles" decision.
  **The decision STANDS; its ordering relative to the tile-performance work is now an OPEN QUESTION (2026-08-17).**
  With `fire_risk_tiles` measured at **117 s**, a 20 s cap converts that layer from "slow but eventually renders"
  into "always fails" until the §10 relation work or the `0030` index lands. Sequencing is not decided here — see
  §7 step 2c.
- **PER-LAYER PARTIAL INDEXES ARE REJECTED — recorded so nobody re-proposes them.** The tile functions select
  the layer **by name through a join**, so no constant `f.layer_id` is ever derived, and a partial index
  predicated on `layer_id = '<uuid>'` can never be chosen by the planner. One composite
  `gist (layer_id, geom)` partial on `status = 'published'` is the shape that works.
- **The pulse's standing-failure signal is a dead-lettered WORK ITEM count, never `job_run.status`.** See §8
  (2026-08-16 review) for the two-directional proof. Corollary, binding: **an operator cancellation must never
  red the tick.**
- **The Railway cap is not lowered until measured.** Report actual non-reclaimable usage after the restart, then
  propose a number with burst headroom.
- **Additive DDL may be applied to prod directly; destructive DDL needs owner approval.** Established this
  session and honoured throughout.
- **Lanes self-heal via the ledger + three crons per lane family** (`docs/layer-lane-standard.md` §8). No agent
  babysits a backfill. Owner call 2026-08-15.
- **Hypertable conversion is its own planned migration**, never a step inside another workstream. See §5.
- **NEW 2026-08-17 — `drizzle/0030` is LANDED AND KEPT PERMANENTLY.** Owner overrode §10's own recommendation to
  skip it. The 400–600 MB carrying cost with one thin reader is accepted; disk is explicitly not a constraint.
  Settles §10 decision (a). See §12.5.
- **NEW 2026-08-17 — refresh incrementally, never recompute.** Owner directive. Continuous aggregates are the
  named mechanism but are **blocked** (no hypertable exists; `geo.features`' day is a jsonb function, not a
  column). The approved route is **delta refresh on the existing `agri.matview_refresh_state` watermark** —
  same O(changed) property, no conversion. **Full reasoning and the four structural blockers in §12.3–12.4.**
- **NEW 2026-08-17 — offline sync is a byproduct of scrubbing, NOT a bulk-sync button.** Approved shape in §11.
  Do not reintroduce a range picker or a prefetch-everything affordance.
- **NEW 2026-08-17 — RAM, not disk, is the constraint.** Owner stated it plainly. This retires size-based
  arguments against §10's relations and against 0030, and sharpens every remaining one onto working set.
- Settled earlier and still binding: maintenance is a third pass inside `jobs-pulse` (order is load-bearing —
  reconcile settles → plan-gaps measures → validate-streams last); **no dedicated ML service**; agri is a local
  CLI; we persist everything we serve.

---

## 4. Assumptions

Ordered by reversal cost, highest first.

- **The pulse will go RED every hour until prod's buried `matview-refresh` work items are cleared.** · default
  taken: ship the honest signal · to reverse: expensive — it is the whole point. The four still-failing
  matviews (§2) are real failures; the count clears when an operator requeues or cancels those items. Nothing
  in the codebase requeues a dead-lettered item today (`sql/ingest/reopen_gap_windows.sql` reopens only
  *succeeded* windows, and gap planning **reports** a dead-lettered day rather than converting it), so the
  clearing action is a hand-written `UPDATE` until a requeue verb exists.
- **No visual/interaction pass has ever been completed on the map, so §9's D1/D3/D4/D5/D9 are code-traced, not
  eyeball-confirmed.** · default taken: report them as CONFIRMED-from-code, because each is a read of the shipped
  control flow with a file:line, not an inference from symptoms · to reverse: cheap — one browser session in a
  **visible** window (see §5's `visibilityState` trap; the automation tab stayed `hidden`, rAF stayed suspended
  and MapLibre never painted, which is why the pass did not happen). These five are the ones a visual pass would
  confirm or refute fastest. The owner's "fire-detections come back eventually" observation is consistent with
  §9's tile latencies but was never timed end-to-end in the running app.
- **§10's polygon relation sizes and two of its row counts are estimates, not measurements.** · default taken:
  proceed with order-of-magnitude figures (150–400 MB, 30–120 MB, fire grid tiers ±5×) · to reverse: cheap
  read-only census. `fire-perimeters` and `evacuation-zones` have **never been censused**; the 740 B/row average
  from `geo.features` is dominated by 3M small fire points and understates polygons badly. **No `EXPLAIN` was run
  against prod in the UI-defect pass** — §10 stands on this runbook's existing measurements plus the `0030` header
  EXPLAIN.
- **`to_regclass` contract has no disposable-DB test (MEDIUM #6, deferred).** · default taken: recorded, not
  written · to reverse: cheap. It false-passes on indexes and sequences (any relkind resolves) and on a
  matview created `WITH NO DATA`. Preflight therefore proves *a relation of that name exists*, not *that
  relation is usable*.
- **The dead-letter census is unbounded in time.** · default taken: count every dead-lettered item of a
  definition, across every run it ever opened · to reverse: cheap, but adding a recency window would
  reintroduce a second notion of failure. A lane with an old, genuinely-abandoned burial stays red until
  someone settles it — which is the intended, operator-reachable behaviour.
- **`npm test` stays broken and unfixed.** · default taken: leave `package.json` alone, invoke `vitest` without
  `--configLoader runner` · to reverse: trivial edit — but first check whether the Docker build gate depends on
  `npm test`, because if it does it has been failing for reasons unrelated to any code.
- **The 17% lifetime transaction rollback rate (104,968 of 510,806) is not investigated.** · default taken:
  recorded, not chased · to reverse: cheap to look at, unknown what it reveals.
- **The 454 stuck `firms-archive` work items stay queued.** · default taken: untouched this session · to reverse:
  cheap to drain, but they drain *into* the throttled box — do it after the memory work, not before.
- **`ix_features_updated_at` (newly built) is kept.** · default taken: keep · to reverse: cheap drop, but
  `idx_features_layer_updated_at` has 9,595 real scans so the access pattern is genuine.
- **`geo.mv_soil_survey_grid` is kept despite having no reader.** · default taken: keep alongside the union fix ·
  to reverse: cheap drop; it costs 339s per refresh.
- **`services/agri-data-service/ml/` stays uncommitted.** · default taken: untracked · to reverse: cheap to
  commit, but a `git add -A` would sweep it into an unrelated commit — stage explicit paths only.

---

## 5. Environment & gotchas

### NEVER RUN PLANTGEO LOCALLY

**Standing owner rule, established 2026-08-17.** PlantGeo is not to be started on the workstation — **especially
the data-warehouse stack**, which destroys the machine. There is no "just this once" exemption for reproducing a
UI bug. Test against production:

- app · `https://plantgeo.aevani.com`
- tiles · `https://plantgeo-martin-production.up.railway.app`

Everything in §9 was gathered this way — `curl`, browser instrumentation and read-only code trace. No local
process, no local database, no write.

### A Martin tile problem MUST be reproduced with an `Origin` header

**`curl` sends no `Origin`, so a total CORS outage returns a clean `200 OK` and looks perfectly healthy.** That is
exactly how the 2026-08-17 outage (§2) survived every prior server-side investigation. Always send the browser's
real origin:

```
curl -sSI -H "Origin: https://plantgeo.aevani.com" \
  https://plantgeo-martin-production.up.railway.app/fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles/6/11/22
```

**Read the response for `access-control-allow-origin`.** A `200` with `vary: … Origin …` and **no**
`access-control-allow-origin` is the signature: the CORS layer is active and did not match. Confirm with an
`OPTIONS` preflight — a mismatched origin returns **400 Bad Request**.

**FIXED IN THE REPO 2026-08-17 (not yet deployed).** `cors.origin` now carries **two independent placeholders**,
each with a correct-by-construction default, so the origin *count* is structurally fixed by the file and cannot
silently collapse:

```yaml
origin:
  - "${TILE_CORS_ORIGIN:http://localhost:3001}"
  - "${TILE_CORS_ORIGIN_RAILWAY_DOMAIN:https://plantgeo-main-production.up.railway.app}"
```

`TILE_CORS_ORIGIN` is untouched, so the already-correct live value keeps working. The new var needs **no Railway
variable at all** — its YAML default already equals the real domain — so the fix is self-sufficient from code.

**MARTIN IS v1.10.1, NOT v1.4.** `Dockerfile.martin:1` and `docker-compose.yml:52` pin v1.10.1; the project
`CLAUDE.md` stack list says v1.4 and is **wrong**. Verified against Martin's source at tag `martin-v1.10.1`:
`config/file/cors.rs` types `origin` as `Vec<String>` matched by **exact string equality** per entry, and
`config/primitives/env.rs` expands env vars into the **raw YAML text before parsing** using the `subst` crate
(pinned v0.3.8), which substitutes **one placeholder → one scalar and never comma-splits.**

**THE NAIVE FIX IS WORSE THAN THE BUG — recorded so nobody tries it.** Making `TILE_CORS_ORIGIN` a
comma-separated list produces **one literal `"a,b"` origin string that matches no real browser `Origin` header**,
silently blocking **both** domains — behind a still-clean `curl` 200. That is a bigger outage than the current
one, with the same invisible signature.

**DEPLOY REQUIRES A REBUILD, NOT A RESTART.** `martin.yaml` is `COPY`'d into the image at build time
(`Dockerfile.martin:3`), not runtime-mounted. `railway service restart` reuses the already-built image with the
old config baked in and **will appear to do nothing.** Push to `main`, confirm Railway rebuilds
**`plantgeo-martin`** specifically, then re-run the two-origin curl check.

- `infra/martin/martin.yaml:7-10` accepts a **LIST** of origins but only **one** is interpolated from the env var:
  `- "${TILE_CORS_ORIGIN:http://localhost:3001}"`. Whatever single value `TILE_CORS_ORIGIN` holds is the only
  allowed origin; every other active domain on `plantgeo-main` is blocked (§2 caveat).
- **Three docs files still carry stale values** — `docs/deployment.md:383` and `infra/railway/README.md:98` document
  the old `https://plantgeo-main-production.up.railway.app`, and `.env.example:61` / `docker-compose.yml:56` default
  to `http://localhost:3001`. Do not treat any of them as the prod truth; read the Railway variable.
- One CORS failure blanks **everything**: `src/lib/map/sources.ts:28-32` puts all six function sources in one
  composite, so a single blocked response fails the whole TileJSON.

### Production access

- **Prod DSN:** `DATABASE_URL_SYNC` in `services/agri-data-service/.env` (gitignored). Values live there only —
  never copy them into docs, logs, shell lines or commits.
- Query with `services/agri-data-service/.venv/Scripts/python.exe` — it has **psycopg2, not psycopg v3**, so use
  `conn.cursor()`, never `conn.execute()`. System python has no driver.
- CLI verbs: `env -u DATABASE_URL LOCAL_SOURCE_LOADER_DATABASE_URL="$DSN" .venv/Scripts/agri-cli.exe <verb>`.
- Prod is **PostgreSQL 18.4, PostGIS 3.6.4, TimescaleDB 2.29.0**. Railway project **Aevani**
  `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. **Railway MCP is
  authenticated as the owner again as of 2026-08-17** — the expiry that blocked the Martin `statement_timeout`
  change (§2) is over, Martin restarts are automatable again, and **the change is unblocked but still NOT
  applied** (§7 step 2c).
- **`SET statement_timeout` explicitly on every long statement.** Redirect long output to a file and use
  `python -u`; piping a background run through `tail` yields an empty file until the process exits.

### The memory picture — read before "fixing" the gauge

- **The 3 GB reading is page cache and cannot fall.** Six proofs: `shared_buffers` is only 256 MB (8.5% of cap);
  2 client backends at sampling; 99.98% of limit during a 150s window with 35 transactions and 1.2 MB read;
  post-restart refill 0.051 GB → 2.98 GB in ~50 min from a 42 GB data dir; no OOM signature; Railway counts page
  cache via cgroup. **It will read ~100% at any cap you set.**
- Verdict refined: **(b) ordinary cache fill for the steady gauge, (a) genuine pressure during bursts.** A pulse
  burst previously read **1,203 MB in a single 15-second interval**, then ~6 minutes of near silence. So a lower
  cap is riskier than a pure-cache reading suggests — bursts hit the ceiling, not the idle baseline.
- **Judge changes by idle/burst window sampling, never the lifetime figure.** Lifetime cache hit is 30.96% over
  37.5 billion reads with `stats_reset` NULL; one session cannot move it.
- **Base memory is autovacuum, not cache:** `autovacuum_max_workers = 10` × `maintenance_work_mem = 128 MB` is up
  to **1.28 GB** non-reclaimable with zero users. That is the cut approved in §3.
- **TimescaleDB is loaded and delivering nothing** — one hypertable, `tracking.positions`, **0 chunks / 40 kB**,
  no compression, retention or CAgg policies. It costs a launcher plus scheduler workers.

### THERE ARE ZERO USEFUL HYPERTABLES

`agri.signal_observation` is a **plain heap table**. Nothing ever called `create_hypertable` (repo-wide grep
returns nothing; `plans/postgresql-18-migration-rehearsal-2026-08-02.md:50-51` recorded 0 hypertables).
Therefore **continuous aggregates and columnar compression are both unavailable.** Conversion is not a quick win:
`create_hypertable(migrate_data => true)` over 46M rows / 26 GB on a 3 GB cap rewrites the whole table, and
**`pk_signal_observation PRIMARY KEY (id)` is illegal on a hypertable partitioned by `observed_at`** — a
hypertable's unique constraints must contain the partitioning column, and `id` carries FKs.

### Compression conflicts with backfill

If hypertable conversion ever happens: compressing old chunks blocks or badly slows inserts into them, and this
service's whole data-completion workstream is historical backfill. `compress_after` must sit beyond the backfill
horizon, which today is most of the table.

### Layer populations — "21 layers" matches nothing

`LAYER_REGISTRY` has **27** toggles (`layer-registry.ts:171-433`), `geo.layers` has **11** rows, the slider
capability catalogue publishes **24** entries (11 + 13 non-features streams). Scope uniformity work against
27/24. **Nothing on the layer side is deletable** — all 11 `geo.layers` rows have a real producer and real rows,
down to `interventions` at 2 features (kept: `conductor/tracks/community_engagement_completion_20260805` is
active). **Do not drop the 16 empty `agri.forecast_*` governance tables** — they are the upstream of
`mv_forecast_ml_daily_serving`.

### THE DISPOSABLE-DB TEST GATE WAS DARK FOR A WHOLE ALEMBIC REVISION

**Found 2026-08-17.** `tests/conftest.py:31`'s `EXPECTED_ALEMBIC_HEAD` **was never bumped when alembic
`20260816_0024` landed.** `_assert_head_and_safe` **hard-fails** (does not skip) when `AGRI_TEST_DATABASE_URL`
is set and the revision mismatches — so every `agri_db`-marked test **refused any database actually at head**
for the entire window 0024 and 0025 were live. Now bumped to `20260817_0025`.

**This is the mechanism behind §4's "2494 passed unset vs 2536 set" discrepancy.** The gate did not fail loudly;
it stayed quiet only because **nobody ran the DB-backed sweep against a genuinely current head during that
window**. A gate that is dark and silent is worse than one that is red.

**22 test files depend on `agri_db_dsn` / `agri_db_async_dsn` / `agri_db_connection` and are all candidates for
the same rot.** Repairing the pin immediately exposed one:
`test_jobs_pulse_agri_db.py::test_run_jobs_pulse_reaches_a_real_dispatchable_lane_and_a_real_durable_definition_in_one_call`
failed with 4 lanes instead of 2, because `run_jobs_pulse` was called without `include_maintenance=False`
(`include_maintenance` defaults **True**, and `_plan_maintenance_steps` adds a reconcile + plan-gaps step per
durable entry regardless of `lane_filter`). Fixed at the call site.

**And that test carried a latent WRONG-TARGET hazard, which is the real lesson.** `ingest/commands.py:21`
imports `ingest_session` into **its own namespace**, so the test's `_bind_real_ingest_session` helper — which
patches only `jobs_pulse_command.ingest_session` — never rebinds it. Had maintenance actually run,
`reconcile_archive_lane` / `plan_archive_lane_gaps` would have resolved their session through the **real**
`db.engine.ingest_session()`, reading `settings.require_local_source_loader_database_url()` — **a different,
unmocked, env-configured DSN, not the disposable engine.** Monkeypatching a name in one module does not rebind
a `from X import Y` copy taken in another.

**Highest-risk next checks** (both exercised directly by `jobs_pulse_command.py`):
`test_jobs_dispatch_agri_db.py` and `test_strategy_mv_refresh_postgresql.py`.

**Working recipe** — the disposable warehouse is podman container `agri-sweep-db` on host port **5442**, database
`agri_sweep`, and it is now migrated to head `20260817_0025`:

```
AGRI_TEST_DATABASE_URL="postgresql://plantgeo_owner:sweeplocal@127.0.0.1:5442/agri_sweep" \
  PGBIN="C:\Program Files\PostgreSQL\16\bin" uv run pytest tests/<file> -q
```

### Build, test and git traps

- **`npm test` is broken repo-wide.** The packaged script runs `vitest run --configLoader runner`, which fails
  **every** suite with "Vitest failed to find the runner" — verified on files nothing touched. Drop the flag and
  the identical suite passes (1,192 tests).
- **mypy is unconfigured and has never been green.** HEAD baseline is **679 errors across 70 files**, verified by
  running the same interpreter against a detached worktree. There is no `[tool.mypy]` section and no CI gate.
  Diff against the baseline; never treat red mypy as a regression. **Ruff is the real Python gate** and is clean.
- **Never `git add -A`.** Multiple sessions edit this tree. Three things must stay uncommitted:
  `services/agri-data-service/ml/`, the unstaged deletion of
  `services/agri-data-service/docs/research/label-harvest-strategy-2026-08-14.md`, and any runbook artifact.
- **Pre-commit runs lint-staged + `eslint --fix` and exceeds 2 minutes** — allow ≥10 min for `git commit`.
- `stash@{0}` is `codex-preserve-pre-main-integration-20260726`, pre-existing — **never drop it**.
- Repo convention: **batch all edits, then one sweep** —
  `npm run check:data-boundary && npm run type-check && npm run lint && npm test && npm run build` (~340s) plus
  `pytest -q`, ruff.
- A commit appeared on `origin` this session with content byte-identical to a local one (`4d1345e` vs `bb03b08`),
  producing an `ahead 1, behind 1` divergence. Resolved with `git reset --mixed origin/main`. **Something else
  commits and pushes to this repo** — check `git status -sb` before assuming your local branch is authoritative.

### Migration ordering — getting this wrong blocks every deploy

`readiness-migration-contract.test.ts` asserts `migration-contract.ts` matches the journal's **latest** entry, and
landing that pair **before** the migration is live in prod makes `/api/ready` return 503 and fails the Railway
healthcheck. Correct order: ship the `.sql` alone → apply to prod → commit journal entry **and** contract re-pin
in **one** commit → push → **restart Martin** if any tile function changed. The journal legitimately jumps 25 → 27
(0026 was a reverted agri-schema mistake) — do not "repair" that gap.

**Never write the literal drizzle statement-breakpoint marker inside a comment.** `drizzle-orm/migrator.js:16`
does a naive `query.split()` on that string, so even in a comment it cuts the file header in half and kills the
migration with a 42601 syntax error. This bit 0029 and would have broken every deploy.

**`scripts/apply-pre-aggregation.mjs` hardcodes `SET lock_timeout = '5s'`,** which cannot clear a running
`observed_days` census snapshot. The first `CREATE INDEX CONCURRENTLY` died on 55P03 and left an **INVALID**
index that had to be dropped and rebuilt at `lock_timeout = 20min`. Always check `pg_index.indisvalid` after a
CONCURRENTLY build.

### A blank map in a background Chrome tab is a harness artefact, not an outage

A tab that is not the **active** tab of its window reports `visibilityState: "hidden"` **even when
`document.hasFocus() === true`**. Chrome suspends rAF, MapLibre never loads its style, **zero** tile requests
fire, and the page renders an empty map frame — indistinguishable from a total layer outage. This reproduced
exactly in the UI-defect pass and cost the whole visual verification (§4).

**`document.hasFocus()` is NOT sufficient. Assert `document.visibilityState === "visible"` before believing any
blank-map observation.** That is the new detail on top of the existing `plantgeo-hidden-tab-blank-map` finding.

### A backtick inside `LayerTimeSlider`'s CSS breaks the whole build

`LayerTimeSlider.tsx` injects its slider CSS through a **JavaScript template literal**
(`useLayerTimeSliderStyles`). A backtick anywhere inside it — **including inside a CSS comment** —
terminates the string early and the rest of the file parses as broken JavaScript. This happened
2026-08-17: a comment written as `` `filter: drop-shadow(...)` `` produced
`Cannot find name 'drop'`, `Cannot find name 'shadow'` and six `TS1005` errors, and **the entire repo
stopped compiling**. Nothing about the error messages points at a comment. Write that comment block with
no backticks; the file now carries a note saying so.

Same family as the drizzle statement-breakpoint trap (§5, "Migration ordering"): **a marker that is inert
in prose but load-bearing to a naive parser, hidden inside a comment.** When a file's content is embedded in
another language's string literal, comments are not safe ground.

### Traps discovered — do not "fix" these

- **§9 carries an eight-item list of map behaviour that LOOKS like a bug and is correct** — snapshot layers
  drawing their whole record with no slider, vegetation's monthly GIBS composite not moving inside a month, the
  `setStyle` gotcha being absent from the date path, out-of-order clobbering being structurally impossible. Read
  it before opening a map defect.
- **The 1,069 firms "missing" days are governed absences.** Month histogram over 25 years: 68% Dec–Feb, 1.7% peak
  fire season. A fetch defect cannot preferentially fail in winter. `ingest/archive_walk.py:603` states the rule.
- **`surface_shortwave_radiation` is a governed dead layer, not a broken one** — `agri.signal_coverage_audit`
  holds 397 `no_data` windows over all 397 POWER cells, 2022-08-02 → 2026-08-02.
- **`geo.watershed_rollup` is populated (2,162 rows) and `geo.refresh_watershed_rollup()` is never called** by
  anything in the repo. It has been serving stale data since it was built.
- The `soil` toggle's `permanentlyUnavailableReason` (`layer-registry.ts:289-290`) is **stale and wrong** —
  `geo.raster_release` holds 12 rows published 2026-08-10.
- **Do not confuse `geo.features_layer_external_id_unique`** (811 MB, UNIQUE, 23 scans, **KEEP**) with the dropped
  `idx_features_layer_external_id_lookup`. Also keep `idx_features_layer_updated_at` (73 MB, 9,595 real scans) and
  `uq_geometry_version` (400 MB, backs a UNIQUE constraint).
- **The index DDL is deliberately not the obvious one:**
  `ON geo.features (layer_id, geo.feature_observation_day(properties)) INCLUDE (geometry_id) WHERE status = 'published'`
  — **not** `AND geometry_id IS NOT NULL`, because `getMetricAtDate`'s summary *counts* the `geometry_id IS NULL`
  rows and partialling them away makes that count unanswerable.
- ~~**`reconcile.py`'s `session_lock` serializes the shard fan-out**~~ — **removed** with the sharding revert
  (there is no fan-out left to serialize). Kept here only so the finding is not re-raised as still open.
- Two ML labels were silently lost at load because their `condition_envelope` was empty — by design
  (`expert_label_plane.py:128-131`), but nothing warns at authoring time. Harvest doc had 32; prod holds 30.

---

## 6. Key files

### Sharding revert — DONE (was §7 step 1)

`observed_day_shards`, `session_lock` (moot once unsharded) and `_probe_census_shards` are **removed**. Any
note below still naming them is describing the pre-revert tree.

- `services/agri-data-service/src/agri_data_service/ingest/reconcile.py` · `observed_layer_days`,
  `observed_layer_days_in_range`, `MAX_OBSERVED_DAY_ROWS = 50_000`.
- `services/agri-data-service/src/agri_data_service/sql/ingest/observed_days.sql` · the `{layer_scope}` and
  `{day_scope}` slots; the `-- observed_days` dispatch marker must stay line 1.
- `services/agri-data-service/src/agri_data_service/ingest/validation/queries.py` · `_ALL_DAYS_SCOPE`,
  `_DAY_RANGE_SCOPE` (inclusive at both ends), `OBSERVED_DAYS_FOR_LAYER`. **The only place `observed_days.sql` is
  `.format()`-ed.** `MAX_OBSERVED_DAY_ROWS` must stay a package-level global on `validation/__init__.py` — tests
  monkeypatch it there.
- `services/agri-data-service/src/agri_data_service/execution/jobs_pulse_command.py` · `_execute_maintenance_step`,
  `DEFAULT_PULSE_TIME_BUDGET_SECONDS = 600`, and the standing-failure signal: `_COUNT_DEAD_LETTERED_WORK_ITEMS`,
  `read_standing_dead_letters`, `_fold_in_standing_dead_letters`, `FAILING_PULSE_OUTCOMES`.
- `services/agri-data-service/src/agri_data_service/sql/jobs/count_dead_lettered_work_items.sql` · the census.
- `services/agri-data-service/src/agri_data_service/jobs/worker.py` · `slice_summary_is_failing` (one slice's
  landing only — it deliberately reads no run status).
- `services/agri-data-service/src/agri_data_service/ingest/commands.py` · unowned file that wraps reconcile and
  validation for the pulse — a signature change here breaks the pulse at runtime with no file conflict.

### Matviews and refresh

- `drizzle/0029_pre_aggregation_layer.sql` · the nine matviews; `:897-901` header prescribes the geometry repair
  chain, `:918` omits `ST_CollectionExtract(...,3)` — that is the bug in §7 step 3.
- `services/agri-data-service/src/agri_data_service/jobs/matview_refresh.py` · the watermark lane; `:294`
  registers `mv_soil_survey_union`.
- `services/agri-data-service/db/agri/tables/matview_refresh_state.sql` · the refresh-state ledger.
- `drizzle/0023_watershed_zoom_generalization.sql:21,110` · the rollup precedent (MV + refresh function).
- `src/lib/server/services/usda-soil.ts:1047,1148` · documents that `readAggregatedFeatures` and
  `readSummaryFeatures` were **not** repointed at the soil matviews.

### Serving and catalogue

- `src/lib/server/services/{environmental-read-model,regional-context,analytics,usda-soil,tracking}.ts`
- `src/lib/server/trpc/routers/{analytics,jobs,visualization,interventions}.ts`
- `src/lib/map/layer-registry.ts` · 27 toggles; `warehouseLayerName` drives the §9 `LEFT JOIN`.
- `src/__tests__/services/pre-aggregation-catalogue.test.ts` · hand-spelled catalogue assertions.
- `src/lib/cache/AGENTS.md` (read first), `src/lib/cache/query-persister.ts:68` allowlist, `:120`
  `resolveCacheTtlMs`.

### Governance and ML

- [`docs/layer-lane-standard.md`](../docs/layer-lane-standard.md) · **268 lines, the contract.** §5 completeness
  engine · §6 self-healing loop · §7 governed absences in `agri.signal_coverage_audit` · §8 three crons + Railway
  traps · §9 the catalogue `LEFT JOIN` · §13 definition of done · §14 verified environment facts.
- `services/agri-data-service/ml/research/label-plane-readiness-2026-08-14.md` · census, 32 settled decisions,
  plan. **Line-number citations to `recommendation_models.py` (§3.1 → L347-355, §3.3 → L414) are stale.**
- `services/agri-data-service/src/agri_data_service/method/ml/recommendation_models.py` · Phase A landed:
  subject vocabulary on for both kinds, species-trait term groups, drought block in `SITE_COVARIATE_FEATURES`,
  `confidence_weight / instance_count`.
- `src/lib/server/db/migration-contract.ts` + `drizzle/meta/_journal.json` + `docs/pending-migrations/README.md`.

---

## 7. Continuation plan

### HANDOFF STATE — 2026-08-17, session ended on context exhaustion. Start here.

**SHIPPED AND VERIFIED IN PROD: `de3139e`** (55 files, +7,569/−426, pushed to `main`). Contains the C2/D1/D3/D4/
D5/D7/D10/D12 client fixes, the offline sync-on-scrub feature (§11), the uniform request budget, the two-origin
`martin.yaml` fix, and **the satellite default that had never shipped**. Verified after deploy: `/api/ready` 200
in 0.41 s, and **Martin now returns `access-control-allow-origin` for BOTH origins** (the raw Railway domain was
hard-blocked before). Sweep before commit was fully green — 1,320 JS tests, 3,057 pytest, ruff, build, `tsc`.

**NOT VERIFIED, and this is the biggest gap:** every *visual* behaviour in `de3139e` is unconfirmed — the
satellite basemap, the synced-days track, the pending indicator, the D1 date filter. The automation tab reported
`visibilityState: "hidden"` on three attempts, which suspends rAF so MapLibre never paints and **zero tiles of
any kind fire** — do not read a blank map or a zero tile count as evidence of anything (§5). **This needs one
session in a foregrounded Chrome window with the tab active.** It is the highest-value cheap next action.

**AGRI BATCH: both NO-GO blockers are FIXED. Uncommitted, not applied, and it needs a SECOND REVIEW PASS** — the
author explicitly declined to self-approve after a NO-GO, which is the right call.

- **Blocker 1 fixed structurally, not by documentation.** The migration is **split so the dangerous ordering is
  impossible**: `drizzle/0031_observation_day_axis.sql` creates the axis `WITH NO DATA` and **reads nothing and is
  read by nothing** (zero blast radius); `drizzle/0032_observation_day_census_repoint.sql` does the
  `CREATE OR REPLACE VIEW` **behind a precondition on `pg_class.relispopulated`** — deliberately not
  `to_regclass`, which resolves for a `WITH NO DATA` matview and would pass while handing production a 500.
  Three rolled-back prod proofs: 0031 alone leaves the census readable at 11,231 rows; 0032 against an
  unpopulated axis **raises as designed**; after populate the column OIDs are identical
  (`25,1043,1082,20,20,20,1184,3802`) and the census reads 11,231 rows with **0 ghost days**. The wrong FULL-JOIN
  rationale is deleted; the FULL JOIN is kept and justified honestly (the two relations refresh on *different
  cadences*, so each can legitimately know a `(surface, day)` the other does not).
- **Blocker 2 fixed and the test is proven to have teeth.** `has_failures` now precedes `budget_exhausted`
  (`matview_refresh.py:1195-1198`); reverting the branch order makes the new test **fail**, so it is not
  decorative. Two tests, so the fix cannot over-reach: a budget-exhausted tick with a standing failure must be
  `failed`, and a *clean* budget-limited tick must still park.

**THE AUTHOR'S OWN HEADLINE CLAIM WAS WRONG BY 13×, self-found and retracted. Read this before budgeting the
apply window.** The axis populate measured **286.8 s on prod, not the extrapolated ~22 s** (11,231 rows, against
the combined statement's 283,049 ms). The 0.82 s/layer extrapolation assumed the *aggregate* dominates; at 5M rows
the **seq scan and the `properties` detoast dominate and both variants pay them**. Consequences:

1. **The re-grain is NOT a latency win.** It is a **spill-width** win (33 B/tuple vs 511 → ~165 MB vs ~1.4 GiB)
   and a **reliability** win (287 s completes under a 900 s cap; it never will under 300 s). **Total refresh
   seconds per day go slightly UP.** That is a deliberate trade of seconds for peak allocation and correctness —
   which is exactly the right trade given §12.6, but it must not be sold as "faster".
2. The author **had freshly reintroduced the very defect it was fixing** — the axis spec shipped with
   `statement_timeout_seconds = 300` against a 287 s statement, 96% of cap. Now 900 s.
3. **The next real lever, recorded rather than guessed:** `ix_features_layer_observation_day` covers every column
   the axis reads; the only thing forcing a heap scan is the fire-perimeters `properties` COALESCE guard. An
   index-only branch for the ten layers carrying no `fireDiscoveryDateTime` could remove the detoast outright.
   DDL redesign, needs its own EXPLAIN, **not attempted.**

**Other corrections now in-tree:** every figure re-read live (**791 s/tick doomed work, FOUR views**, soil at
86,320 / 104,269 ms); the `Parallel Hash` rationale replaces the false "parallel workers = the leak" story, with
the rule stated as **"does the plan contain `Parallel Hash`?"** not "is it parallel?"; `= 1` is documented as
*what shipped, not a mitigation*; wide census cadence 86,400 → **21,600** (six-hourly, keeping the
`newest_observed_at` **status driver** under a day); budget 2,400 → **2,100** and lease 3,000 → **2,700** (worst
tick ~32 min, ~25 min clear of the hourly cron); `mv_signal_cell_daily` 1,800 → **1,900**;
`SUCCESSFUL_REFRESH_OUTCOMES` exported so the reset rule is defined once; and **a new
`tests/test_alembic_head_pin_contract.py`** that makes the §5 dark-gate class impossible (note its regex needs
`(?:\s*:[^=]+)?` — this tree mixes `revision = "..."` and `revision: str = "..."`, and the bare-form-only version
silently skipped 0001 and derived the wrong head).

**Gates:** ruff clean · mypy 2 pre-existing · **160 passed / 1 skipped** · declarative parity green · 2 separators
each in 0031/0032 with **0 in prose** · **0** `CONCURRENTLY` outside comments.

**APPLY ORDER — do not improvise, and budget ~5 minutes for step 3, not seconds:**
1. `alembic upgrade head` → `20260817_0025`. **Before the Python deploy** — the shared upsert writes the two new
   columns unconditionally, so the ledger write fails until they exist. Additive, `IF NOT EXISTS`, re-runnable.
2. `drizzle/0031_observation_day_axis.sql`. Safe at any time; nothing reads the new relation.
3. **Same window:** `SET statement_timeout='900s'; REFRESH MATERIALIZED VIEW geo.mv_feature_observation_day_axis;`
   — non-concurrent (CONCURRENTLY is illegal on an empty matview). **~287 s.** Then assert `relispopulated = true`.
4. `drizzle/0032_observation_day_census_repoint.sql`. **Refuses if step 3 was skipped or failed.**
5. **One commit:** journal entries for **both** 0031 and 0032 + `migration-contract.ts` re-pinned to 0032. Not
   before 2–4 are live. `0030` stays authored-but-unapplied.
6. Deploy the Python. **No Martin restart** — no tile function changed.
7. Clear the 15 standing dead letters when green is wanted; the three still-broken views keep it red via
   `deferred_failing`, which is intended.

**Owner decisions still open:** whether to commit the pre-existing pulse batch (~456 lines in
`jobs_pulse_command.py`, deliberately not swept into `de3139e`); whether to clear the **15 standing dead letters**
that keep the hourly pulse red; §10 decision (c) on `tile_interventions` gaining a day column.

**Cheapest high-value next actions, in order:** (1) the browser validation pass above; (2) close the two agri
blockers and apply in the revised order; (3) `drizzle/0030` — approved to land and keep, 25–60 min out of band,
`btree_gist`, `lock_timeout = '20min'`, verify **both** `indisvalid` and `indisready`, DROP any INVALID leftover
first; (4) §10's tile relations, which are the real answer to §12.6; (5) the 5-line alembic-head parity test that
makes the §5 dark-gate class impossible.

**REPRIORITISED 2026-08-17 by owner directive. Work the A-block first; the numbered legacy plan below it is
unchanged and still correct, but is no longer the top of the queue.**

- **A1. The cheap client fixes, as one batch.** C2 (one `setQueryData` in `revalidateAgainstDW` — every
  allowlisted layer is permanently one fetch behind), D1 (`applyDateFilter` onto the `styledata` convergence
  path), D5 (`placeholderData: keepPreviousData` **plus the loading state §11.1 step 2 requires** — one change,
  not two), D4 (caption from `resolvedDate`, not the slider day), D3, D7, D10. Ordered cheapest-first in §9.
  **These are what make the map trustworthy; nothing downstream is judgeable until they land.**
- **A2. Commit and deploy the satellite default** (§11.6). It is in the working tree and has never shipped —
  `HEAD` still opens on `dark`. Decide separately whether `currentStyle` should persist across reloads.
- **A3. Verify the service worker actually installs** (§11.7). Code-traced dead, not observed. One browser
  session. **Do this before building anything that assumes offline works.**
- **A4. The sync index** (§11.2) — stamp `layerId` + `day` onto `StoredLayerQueryEntry`, bump
  `CACHE_SCHEMA_VERSION`, expose enumeration. **Everything else in §11 is blocked on this.**
- **A5. The synced-days track** (§11.3) and **per-timeline reset** (§11.4).
- **A6. The uniform rate limiter** (§11.5) — one client-side limiter over the ~16-key scrub fan-out, covering
  `/api/fires`' raw-fetch path too. Do not extend either server-side Redis limiter.
- **A7. The two census matviews via delta refresh** (§12.4). **Coupled to §11:** until a newly-ingested day is
  selectable, the sync track will faithfully cache a frozen axis. This is also the C3 causal spine.
- **A8. Then the DB block below** — 0030 (now approved to land and keep, §12.5), §10's relations, the Martin
  timeout **after** the latency work, and the two-origin `martin.yaml` fix.

1. ~~**Revert the sharding.**~~ **DONE.** `observed_layer_days` is a single unbounded statement again (68.7s vs
   95.3s, block reads a wash, 57% of the 120s timeout); `session_lock` and `_probe_census_shards` are gone.
   `{day_scope}` in `observed_days.sql` and `_DAY_RANGE_SCOPE` in `validation/queries.py` were kept on purpose —
   bounded day ranges are what the new expression index makes fast, so the *slider* still wants them, and
   `MAX_OBSERVED_DAY_ROWS` still lives on `validation/__init__.py` where the tests monkeypatch it.
2. **Land the tile index, then the Martin timeout — in that order.**
   a. Build `ix_features_layer_geom` out of band per §2 ("Migration authored, NOT applied"): `btree_gist`,
      `lock_timeout = '20min'`, verify `indisvalid` **and** `indisready`, DROP any INVALID leftover before a
      retry. Re-measure a `geo.*_tiles()` call; the target is the 45.6s BitmapAnd disappearing.
   b. Only once it is live in prod: commit `drizzle/0030_features_layer_geom_tile_index.sql`, the journal entry
      and the `migration-contract.ts` re-pin **in one commit**. Landing that pair early 503s `/api/ready`.
   c. Append `options=-c statement_timeout=20000` to `DATABASE_URL` on **`plantgeo-martin` only** (**unblocked** —
      the Railway MCP is authenticated again as of 2026-08-17; it is simply not done). A tile that would take 45s
      then fails fast instead of holding one of 8 pool connections. Note §9's measurement: cold composites run
      **84–117 s**, so a 20 s cap will shed real tiles, not just pathological ones — that is the intent, but the
      blank-layer consequence via `sources.ts:28-32` must be expected.
      **NEW RISK, ordering (2026-08-17): `fire_risk_tiles` is measured at 117 s.** A 20 s cap converts that layer
      from "slow but eventually renders" into **"always fails"** until step 2a's index or §10's relation work lands.
      Sequencing therefore matters — apply the cap **after** the latency work, not before, or accept a hard
      `fire-perimeters` outage in the interval. **Flagged, not decided** (§3).
   d. **Make `martin.yaml` list BOTH origins — the durable fix for §2's CORS outage.** Today `cors.origin` takes one
      interpolated entry, so setting `TILE_CORS_ORIGIN=https://plantgeo.aevani.com` (applied to prod this session)
      **blocks** `https://plantgeo-main-production.up.railway.app`. Change `infra/martin/martin.yaml:7-10` to a
      two-element list, then **deploy `plantgeo-martin`** — repo change + deploy, **NOT done**. In the same pass
      correct the stale documented value at `docs/deployment.md:383` and `infra/railway/README.md:98`.
3. **Base-memory cuts are ON HOLD, not pending application** — see §3. Do not `ALTER SYSTEM SET
   autovacuum_max_workers = 3` until autoanalyze on `geo.features` has been observed keeping up, and do not cut
   `max_connections` before a real concurrent-connection count exists (it pulls against Martin's `pool_size`).
   Both still require a full restart, not `pg_reload_conf()`, when they do happen.
3. **Fix `geo.mv_soil_survey_union`.** It fails to build with
   `GEOS TopologyException: unable to assign free hole to a shell at -121.5955,42.6522`, and
   `matview_refresh.py:294` retries and fails on **every pulse**. The file's own header (`0029:897-901`)
   prescribes snap → MakeValid → **CollectionExtract**, but the `delineation` CTE at `:918` applies only
   `ST_MakeValid(ST_SnapToGrid(f.geom, 0.000001))`. Add `ST_CollectionExtract(..., 3)` in a new migration, rebuild
   the matview, then **repoint `usda-soil.ts:1047` at it** — today it has no reader, which is why the failure has
   been costless.
4. **Measure base memory and propose a cap.** After the restart, report actual non-reclaimable usage:
   `shared_buffers` + peak backend `work_mem` + autovacuum workers + TimescaleDB workers. Sample an **idle window
   and a pulse-burst window separately** — the burst is what a lower cap would hit. Then recommend a specific cap
   with headroom. Do not lower it before this number exists.
5. **Re-grain `geo.mv_signal_cell_daily`.** 6,349 MB / 24,968,939 rows / 25-minute refresh for only a ~1.8×
   reduction, because its grain `(support_key, signal_name, normalized_unit, cell_id, observed_day)` is nearly the
   source grain. Keep the release-winner dedup (`DISTINCT ON ... release_retrieved_at DESC`) that makes reads
   cheap; cut the width. Re-measure its size and refresh time after.
6. **Resume the layer-lane conformance backlog** — the 12-item ranked list in §8's 2026-08-15 entry. Highest
   blast radius first: the **454 `firms-archive` work items queued since 2026-08-08** (drain *after* step 2, since
   they drain into the throttled box); the **signal plane being on no schedule at all**; `coverage_fill`'s
   **filesystem `Path.exists()` idempotence key**, which is not durable on an ephemeral cron container; and the
   **agent drought tool reading the 0-row `drought_polygon_snapshot`** while the map serves `geo.drought_areas`.
7. **Clear the deferred review findings** (full list in §8): a disposable-DB contract test for `to_regclass`
   semantics (MEDIUM #6), repointing `routes/ops.py` at `jobs/lease.py::failure_condition_name` (MEDIUM #7), an
   EXPLAIN that actually demonstrates the index-causation claim (MEDIUM #4), and the structurally-unreachable
   `MAX_OBSERVED_DAY_ROWS` cap on the one-layer path (MEDIUM #5, pre-existing).
8. **Then the deferred workstreams:** USDM and ERA5-Land as self-healing lanes, the persistence audit, the FIRMS
   `INGEST_MAX_SOURCE_RECORDS` decision, ML Phase B. Phase B is **hard-blocked** until drought covariates exist —
   drought features are now required inputs, so a missing covariate index 36–38 makes `build_design_row` return
   `None` for every row.

---

## 8. Session log

### 2026-08-17 — owner directives folded in; client batch COMMITTED AND PUSHED as `de3139e`

**The client half is committed and pushed: `de3139e`, 55 files, +7,569 / −426, `890f430..de3139e`.** Pushing to
`main` is the deploy path, so this is live-or-deploying. **The agri half is deliberately NOT committed** — it
carries a drizzle migration and must follow the ship→apply→pin ordering (§5), and its adversarial review was
still open at commit time.

**Sweep, all green:** `check:data-boundary` · `type-check` · `lint` · **1,320 JS tests passed / 13 skipped** (up
from the 1,192 baseline, so ~128 new) · `npm run build` · `tsc --noEmit` exit 0 · **`ruff` All checks passed** ·
**`pytest` 3,057 passed / 105 skipped** (§4's old "2494 unset vs 2536 set" figures are superseded).

**Also in `de3139e`, beyond the defect fixes:** the satellite default (which had never shipped — `HEAD` opened on
`dark`), the two-origin `martin.yaml` fix, and the `CLAUDE.md` correction that Martin is **v1.10.1, not v1.4**.
**`martin.yaml` is `COPY`'d into the image at build time, so this needs a genuine REBUILD of `plantgeo-martin`;
a restart reuses the old baked-in config and appears to do nothing.**

**Deliberately left uncommitted:** `conductor/RUNBOOK.md`, `services/agri-data-service/ml/`, the
`label-harvest-strategy-2026-08-14.md` deletion, the whole agri tree, and `drizzle/0030`/`0031`. **The
pre-existing pulse batch (`jobs_pulse_command.py` and friends, ~456 changed lines) is also still uncommitted** —
it is a separate concern from an earlier session and was not swept in.

**Judgement call recorded:** `map-store.ts`, `ServiceAreaLayer.tsx` and `coverage-region.ts` carried an *earlier*
session's uncommitted viewport work. They went in because `map-store.ts` is where the satellite default lives, so
excluding it would have dropped the owner's request.

Seven parallel lanes on disjoint file boundaries; the partition held with zero collisions. **All four client
lanes were adversarially reviewed and all four returned CHANGES-REQUIRED** — see the §2 ledger.

**Owner decisions taken:** land AND keep `drizzle/0030` permanently · refresh incrementally rather than
recompute (continuous aggregates named; see §12.3 for why they are blocked and what replaces them) · offline
sync is a **byproduct of scrubbing**, not a bulk button (§11) · **RAM is the constraint, disk is not.**

**Landed in the working tree:**

- **C2 FIXED — the highest value-per-line change in the batch.** `revalidateAgainstDW` persisted to IndexedDB
  and dropped the result into a floating promise; nothing ever called `setQueryData`, so **every allowlisted
  layer rendered exactly one fetch behind, permanently.** Fixed with `createIndexedDbLayerQueryPersister(getQueryClient)`
  — a factory closing over the client, injected from `providers.tsx`. `query.setData()` was **rejected**: it is
  a `query-core` class method, not app-facing API, and skips the client's `structuralSharing`. A module-global
  client was rejected as hidden mutable state that binds to whichever client registered last.
- **The sync index (§11.2) is built.** `src/stores/sync-index-store.ts` ships the pinned contract verbatim:
  `useSyncedDays` · `useSyncIndexReady` · `useLayerSyncedBytes` · `clearLayerSyncedDays` · `useHydrateSyncIndex`.
  **`CACHE_SCHEMA_VERSION` was deliberately NOT bumped** — see the correction below.
- **`MAX_TOTAL_CACHE_BYTES` 50 MB → 512 MB, and the enabler matters more than the number.** Every write
  previously read the **entire** cache into RAM just to ask whether the incoming entry fitted. At 512 MB that
  pass is what would have killed the tab. Replaced with a **cursor walk** (`forEachEntry`) plus O(1) running
  totals. **This is the RAM-not-disk distinction applied correctly**: the budget rise is only safe because the
  working set no longer scales with the store.
- **Fires lane: D3, D7 and C5 fixed; D11 and D12 discovered** (§9). D12 in particular — a 304 could repaint the
  **wrong day's** content — would have made D3's new staleness label truthful-sounding but wrong.
- **§11.7's service-worker claim REFUTED against prod** and the section rewritten. Zero code changed.

**CORRECTION to §11.2, which was wrong in this runbook and in the brief derived from it: the `queryHash` IS
reversible.** It is plain `JSON.stringify(queryKey)`, so `JSON.parse` recovers the router path and the whole
input including `date` and the discriminators. A real key, measured from prod:
`[["environmental","getClimateField"],{"input":{"bbox":"-154.726352,29.145296,-81.273648,61.854704","renderForm":"field","signal":"air-temperature","variant":"mean"},"type":"query"}]`.
The shipped design **stamps forward and parses back as a fallback**, which is why no schema bump was needed and
why 504 live entries were not discarded. The `queryHash` format is nonetheless **react-query's internal
contract, not ours** — a minor bump could change it, which is exactly why the stamp exists.

**Measured live in production 2026-08-17 (real browser, `https://plantgeo.aevani.com`):**

| | |
|---|---|
| IndexedDB `plantgeo-query-cache` | **504 entries, 49.57 MB against the 50 MB cap — 99.1% full** |
| expired but still resident | **235 of 504 (47%)** — eviction ran only on write, only by LRU, **never by expiry** |
| service worker | `activated`, **CONTROLLING**; `caches.keys()` = `["plantgeo-v2"]` |
| `/manifest.webmanifest` | **200** (served by `src/app/manifest.ts`, a Next file-convention route) |
| cached paths | `getSoilField` 137 · `getStreamflow` 104 · `getGroundwater` 99 · `getWeatherForBbox` 80 · `getVegetationIndex` 51 · `getDroughtClassification` 30 · `getClimateField` 3 · `getSoilSurvey` **0** · `getWatersheds` **0** |

**Known limitations, accepted and stated rather than hidden:**

- **`useSyncedDays` keys on the day alone, not `(day, bbox)`.** The track means "an answer for this day is on
  disk", **not** "the next read will hit". Driven by D6 — the bbox is serialized at 6 decimal places, so a
  ~10 cm pan mints a new key. If D6 is fixed with a quantised bbox the over-claim narrows automatically.
- **`getStreamflow` and `getGroundwater` both attribute to the `water` toggle.** Two feeds, one toggle — so a
  day holding only one of them still reads as synced. Flagged for adversarial review as a possible user-visible
  over-claim.
- **Dateless entries never appear on any track.** `useClimateFieldQuery` passes `date: undefined` whenever a row
  sits at server-today, so those entries take the 5-minute live TTL and name no day. Stamping them "today" would
  become a lie at UTC midnight. Consequence: **a layer sitting exactly at server-today will not light up.** Per
  the staleness table this affects only the 1-day-lag group; the 11 layers that are 11–15 days stale open on a
  historical day and **do** attribute.
- **Revalidation policy changed to none-at-all on historical days, ≤1/min otherwise.** **This is under
  adversarial review as a possible C1 regression** — the agri pipeline *does* rewrite historical days, so a
  corrected day could stay stale for the full 30-day TTL with nothing to notice. Do not treat as settled.

**API break, repo-wide:** `indexedDbLayerQueryPersister` **no longer exists**. Use
`createIndexedDbLayerQueryPersister(getQueryClient)`.

### 2026-08-16 (late) — 0024 applied, map diagnosed, pulse review fixes (UNCOMMITTED)

**Prod changes applied this session:** alembic `20260816_0024` (creates `agri.matview_refresh_state`) and
`ANALYZE geo.features` + `geo.layers`. Numbers and the recovery evidence are in §2. Nothing else was applied;
`drizzle/0030` and the Martin `DATABASE_URL` change are both authored/decided but **not** applied.

**Adversarial review of the pulse batch: CHANGES-REQUIRED. Both HIGH findings shared one root cause**, and the
diagnosis was **independently re-derived and confirmed** before implementing:

- `_slice_outcome` derived `failed_run` from `JobSliceSummary.run_status`, which is wrong in both directions.
  *(a) It cannot fire when it should* — `select_open_job_run.sql` selects only `'queued'`/`'running'`, so the
  tick after a run rolls terminal selects no run, reports `run_status=None`, and reads healthy. Coverage was
  exactly ONE tick, and that tick was already loud via `dead_lettered > 0`. *(b) It fires when it must not* —
  `refresh_job_run_rollup.sql:98` counts a `'cancelled'` **work item** as `failed` and the run CASE has no
  `'cancelled'` branch, so an operator cancellation rolls the run to `'partial'`/`'failed'`, both of which were
  "terminally failed".
- **Confirmed by grep over every writer:** only two statements touch `agri.job_run.status` —
  `insert_job_run.sql` writes `'queued'`, the rollup CASE writes `'running'/'succeeded'/'failed'/'partial'`.
  **Nothing ever writes `'dead_letter'` or `'cancelled'` there**, so a third of the old
  `TERMINALLY_FAILED_RUN_STATUSES` set was dead code and the "cancelled is excluded" comment was vacuous.
- **Stronger than the review stated:** `jobs/matview_refresh.py` opens ONE never-rotating run by design, so it
  accumulates settled items forever. One historical burial or cancellation pins that run at `'partial'`
  **permanently** — the hourly cron would have exited 1 every hour forever, even with every current refresh
  succeeding, and the old detail line told the operator to "cancel it deliberately", which made it worse.

**Fix as shipped:** new `sql/jobs/count_dead_lettered_work_items.sql` counts `agri.job_work_item` rows in
`'dead_letter'`, joined up to `agri.job_definition.name`, across every run that definition ever opened —
issued **unconditionally per lane per tick** by `_fold_in_standing_dead_letters`, on the healthy path and the
raised path alike, and **failing closed** (an unreadable census leaves the lane `raised`, never `ran`). The
outcome literal `failed_run` became `standing_dead_letters`; `PulseLaneResult` gained a
`standing_dead_letters` count that triggers `failing_lanes` independently of the outcome label.
`TERMINALLY_FAILED_RUN_STATUSES` was deleted and `slice_summary_is_failing` now describes one slice's landing
only. `'cancelled'` is deliberately not counted — that is the safety property.

**Tests rewritten to exercise the runtime path, not the classifier.** `tests/test_jobs_pulse_command.py`'s
fake session now answers the census query for real, so a test states buried work **on the ledger** and lets
the pulse's own code find it. Three complicit tests were replaced: one that hand-built a
`no_claimable_work` + `run_status='failed'` pair the runtime cannot produce after burial; one that
parametrized `'dead_letter'`, never written to `job_run.status`; and one that parametrized `'cancelled'`,
likewise unwritable, while asserting the very safety property finding (b) proves false. New: buried items
stay RED on ticks 1, 2 **and** 3 · operator cancellation keeps the tick GREEN · every writable run status is
green on its own · the census is issued even when dispatch raised · an unreadable census fails closed · a
paused lane is never censused. Plus a real-SQL test in `tests/test_jobs_pulse_agri_db.py` proving the
three-table join counts a `'dead_letter'` item and **not** a `'cancelled'` one (DSN-gated; **not** run this
session — no database was touched).

**Also fixed:** MEDIUM #10 — `jobs/lease.py::failure_summary`'s SQLAlchemy branch now returns through
`clamp_summary()`, restoring the invariant that every return has passed redaction and clamping (not a live
leak; both halves are `__name__`s). LOW #9 — the stale `ingest.reconcile.observed_day_shards` pointer in
`ingest/validation/queries.py` is gone.

**Deferred, by explicit instruction — report only:**

| # | finding | why deferred |
|---|---|---|
| MEDIUM #4 | the index-causation claim is asserted, not demonstrated | needs a DB `EXPLAIN`; out of scope, no DB this session |
| MEDIUM #5 | `MAX_OBSERVED_DAY_ROWS` structurally unreachable on the one-layer path | pre-existing, not introduced by this batch |
| MEDIUM #6 | no disposable-DB contract test for `to_regclass` semantics | false-passes on indexes/sequences and on a matview created `WITH NO DATA` — recorded in §4 as a known gap |
| MEDIUM #7 | `failure_condition_name` duplicated in `jobs/lease.py` and `routes/ops.py` | `routes/` is not this batch's to change; follow-up in §7 step 7 |
| MEDIUM #8 | rationale duplicated across source and `AGENTS.md` | noted only |

**Frontend, same batch:** default basemap is now `satellite` (`map-store.ts`), and `DEFAULT_VIEWPORT` is
**computed** from the coverage bbox via a new optional `NEXT_PUBLIC_INGEST_BBOX` (falling back to the verified
prod values). **It needs a Dockerfile `ARG` to be settable in prod — NOT yet added.**

### 2026-08-16 06:10 — pre-aggregation live in prod; base-memory cuts approved; handoff

- **Applied to prod and registered as `890f430`:** drizzle 0028 + 0029, two `CONCURRENTLY` indexes, 8 of 9
  matviews, 5,392 MB of dead indexes dropped, `effective_cache_size` 768 MB → 2 GB, `work_mem` 4 MB → 16 MB,
  Martin redeployed. Numbers in §2.
- **Measured: sharding is worse.** 68.7s single vs 95.3s across 13 shards, `shared_blks_read` a wash. Revert
  decided.
- **Diagnosed base memory:** the 3 GB gauge is page cache and cannot fall; the real reservation is
  `autovacuum_max_workers = 10` × `maintenance_work_mem = 128 MB` ≈ 1.28 GB. Owner approved cutting workers to 3
  and `max_connections` to 25 with a restart; declined `shared_buffers`/`maintenance_work_mem` cuts and dropping
  TimescaleDB for now.
- **Caught a latent deploy-breaker:** 0029 contained the literal drizzle statement-breakpoint string inside a
  comment, which the migrator splits on — `npm run db:migrate` would have failed on every deploy.
- Resolved an `ahead 1 / behind 1` divergence against a byte-identical commit pushed by something else.
- **Grilling gate answers:** revert sharding first · re-grain `mv_signal_cell_daily` · fix (not drop)
  `mv_soil_survey_union` · measure before lowering the Railway cap.

### 2026-08-15 — pre-aggregation built (`wf_d69f07c0-dd9` → `4d1345e`)

- 11 new matviews + 2 orphans adopted (`watershed_rollup`, `mv_forecast_ml_daily_serving`), watermark-driven
  refresh via a new `agri.matview_refresh_state` ledger, read paths rewired, agent SQL tools repointed, catalogue
  registration extended.
- **Adversarial equivalence verifier refuted the build on 11 grounds, all fixed before commit** — the day-rule
  fork (four-key vs three-key COALESCE) that would have reported observations on days the map renders none; lane
  and unit gates missing from `mv_signal_cell_daily`; two watermarks omitting the clock component their own DDL
  requires; `getRecentActivity` over-counting by up to 59 minutes; an INNER→LEFT join changing the row set.
- Established: **zero useful hypertables**, so continuous aggregates and compression are unavailable; the "21
  layers" figure matches nothing (27/11/24); nothing on the layer side is deletable.

### 2026-08-15 — sharding wave (`wf_1920bfbd-7d9` → `4a685a1`)

- Sharded the observed-day census, surfaced `reopen_exhausted` gap windows in the admin console, rewrote the
  three `geo.mv_strategy_recommendations_*` matviews off fabricated `random()` coordinates onto real
  `agri.spatial_cell` geometry, and landed ML Phase A's four spec changes.
- Adversarial verifier refuted the per-shard truncation claim; two grounds died on contact, three stand
  (structurally unreachable cap, no test coverage, `session_lock` serialization).
- **Layer-lane conformance audit: 0 of 21 conformant**, producing the 12-item ranked backlog now in §7 step 6.
  Headline: 454 `firms-archive` work items queued since 2026-08-08 through ~168 hourly pulses; the signal plane
  on no cron at all; `coverage_fill.write_fill_plan()` using a filesystem path as its idempotence key; the agent
  drought tool answering from a 0-row plane; no pre-aggregation existing at all.

### 2026-08-15 — deploy pushed; self-healing correction

- The two held commits were pushed. Owner corrected the backfill approach: lanes must self-heal via the ledger
  and crons with no agent monitoring, and `docs/layer-lane-standard.md` is the governing contract (decision S5).
  Runbook steps 8 and 9 were rewritten from agent-run backfills into lane-conformance work.

### 2026-08-15 — ML label-plane review merged with the serving/maintenance handoff

- Prod census of the label plane found `expert_label_training_instance` at 0 rows, so the cited 0.025/0.541
  failure is **not reproducible from prod**; found `agri.species` and `companion_relationships` present with the
  needed columns and empty; found the strategy matviews fabricating coordinates with `random()`. 32 owner
  decisions settled across three rounds.

### 2026-08-15 — data-quality loop scheduled inside the pulse

- `3016dca` (maintenance as a third pulse pass) and `c14e36b` (gap reopening capped at 5 generations,
  `reopen_exhausted`), both verified against prod.

---

## 9. The map UI — measured defects (2026-08-16/17)

Evidence: live `curl` and browser measurement against production plus three read-only opus code traces. Nothing
was run locally, no database was written, no code was changed. **A production environment variable WAS changed —
see D0.**

**Evidence-gathering lesson, binding: `curl` sends no `Origin` header.** Every measurement below was taken without
one, which is why a total CORS outage sat underneath all of it returning clean `200`s. Any future tile diagnosis
must send `-H "Origin: https://plantgeo.aevani.com"` and read the response for `access-control-allow-origin` (§5
for the exact command shape). A green `curl` is not evidence that a browser can load the tile.

### D0 CONFIRMED — Martin CORS blocked every dynamic layer (ROOT CAUSE, FIXED 2026-08-17)

**This dominates everything else in this section and is listed first for that reason.** Full detail, IDs and
verification in §2. In short:

- Prod Martin allow-listed `https://plantgeo-main-production.up.railway.app`; the browser origin is
  `https://plantgeo.aevani.com`. `infra/martin/martin.yaml:7-10` interpolates one value into `cors.origin`.
- Martin returned `200 OK` with `vary: … Origin …` and **no `Access-Control-Allow-Origin` header**; `OPTIONS`
  preflight returned **400**. The browser blocked the response.
- `src/lib/map/sources.ts:28-32` composes all six sources into one request, so the single failure **failed the whole
  TileJSON and blanked every dynamic layer at once**.
- The shipped error message blames an expired Protomaps pin. **It is wrong.** PMTiles
  (`https://tiles.aevani.com/pnw-2026-08-02.pmtiles`, `206`, `ACAO: *`, 1,411,574,646 bytes) and AWS terrain
  (`ACAO: *`) were both healthy. **Only Martin was broken.**
- **FIXED:** `TILE_CORS_ORIGIN=https://plantgeo.aevani.com` on `plantgeo-martin`. Verified — header present, console
  error gone, map renders in a real visible browser. **Caveat:** the raw Railway domain is now the blocked one
  (§2, §7 step 2d).

**D0 → D1 linkage, and it reframes D1.** D1 is gated on `map.isStyleLoaded()`, which stays false while any source is
unhealthy. **This CORS outage WAS exactly that unhealthy source, and it was permanent.** So **D1 was firing 100% of
the time in production** — the date filter was **NEVER** applied to any tile-baked layer, not merely "when a source
is slow". **D1's code defect is still real and still needs fixing**: any future slow or failing source re-triggers
it. The CORS fix removed the trigger that was present; it did not remove the fragility.

### Live measurements

**Martin tile latency, per source, cold, PNW.** Composite is
`fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles`.

| source | HTTP | bytes | wall time |
|---|---|---|---|
| `fire_risk_tiles` | 200 | 26,765 | **117.2 s** |
| `sensor_tiles` | 200 | **2,155,849** | **23.8 s** |
| `burn_severity_tiles` | 200 | 87,115 | 6.8 s |
| `watershed_tiles` | 200 | 26,522 | 1.2 s |
| `evacuation_zone_tiles` | 200 | 22,070 | 1.0 s |
| `intervention_tiles` | 204 | 0 | 0.34 s |

`fire_risk_tiles` returns **26 KB after 117 seconds** — the worst work-to-output ratio in the system, and it is
the `fire-perimeters` layer, **not** fire-detections.

**Composite tile latency and payload by zoom** — this is the request the browser actually issues, one composite
URL per tile:

| z/x/y | bytes | wall time |
|---|---|---|
| 5/5/11 | **10,751,237** (10.3 MB) | 23.6 s |
| 6/11/22 | 3,353,118 | **84.4 s** |
| 6/10/23 | 2,893,572 | 19.9 s |
| 6/11/23 | 2,318,321 | 1.6 s (warm — Martin cache primed by the per-source run) |
| 7/22/46 | 1,173,172 | 3.3 s |
| 8/44/92 | 290,622 | 2.9 s |

**Two independent problems; do not conflate them.** *Latency* is 20–120 s per cold tile. *Payload* is a **10.3 MB
single vector tile at z5** and 2–3 MB at z6 — and z5/z6 is where the map opens. Even at zero latency the payload
is a client RAM and decode problem. Warm repeats of 6/11/23 ran 1.6 s then 2.1 s, so Martin's 128 MB /
`tile_expiry: 5m` cache does work — it simply cannot hold a working set of multi-megabyte tiles, so nearly every
pan is cold again.

**Tile responses carry NO `Cache-Control`.** Headers on the composite are `HTTP/1.1 200 OK` + `etag:` only — no
`Cache-Control`, no `Age`. Browser and CDN fall back to heuristic caching, so a returning session re-pays the
multi-MB cold cost. `infra/nginx/nginx.conf` would have set one, but that proxy is commented out of
`docker-compose.yml` and Railway serves Martin directly.

**Live per-layer staleness** — `environmental.getSliderCapabilities` against prod, fetched in 0.47 s (server memo
warm). `serverCurrentDate: 2026-08-17`, `streamsUnavailable: false`, 24 layer entries, `streams: []`.

| lag (days) | layer | temporalKind | earliest | latest |
|---|---|---|---|---|
| — | `soil-survey` | snapshot | null | **null** |
| — | `interventions` | snapshot | null | **null** |
| 2461 | `watersheds` | snapshot | 2019-11-21 | 2019-11-21 |
| 725 | `burn-severity` | event | 2024-08-22 | 2024-08-22 |
| **78** | `climate-field-shortwave-radiation` | daily_series | 2022-04-30 | 2026-05-31 |
| **15** | `soil-field-vpd` / `-temperature` / `-moisture` | daily_series | 2022-04-30 | 2026-08-02 |
| **11** | `climate-field-*` (wind-speed, soil-wetness ×3, relative-humidity, precipitation, dew-point, air-temperature) | daily_series | 2022-04-30 / 2022-08-05 | 2026-08-06 |
| 6 | `vegetation` | daily_series | 2022-08-05 | 2026-08-11 |
| 2 | `evacuation-zones` | snapshot | 2026-06-16 | 2026-08-15 |
| 1 | `weather-observations`, `water-gauges`, `sensors`, `fire-perimeters`, `fire-detections`, `drought-areas` | — | — | 2026-08-16 |

- The 1-day group is **healthy** — yesterday's data on a UTC server day.
- `watersheds` and `burn-severity` are `snapshot`/`event`; their old dates are **correct**, not stale.
- The **11–15 day** group (3 soil-field + 8 climate-field = **11 layers**) is the real staleness the owner sees:
  `daily_series` layers whose newest day is nearly two weeks old.
- `climate-field-shortwave-radiation` at **78 days** is the known governed dead layer (§5) — correct *emptiness*,
  but it still publishes a slider whose track ends 78 days ago with no indication why.
- `soil-survey` and `interventions` report `latestObservedDate: null`.

**App-shell latency, prod.** `/api/ready` 0.43 s · `/api/health` 0.36 s · navigation TTFB 217 ms, DCL 967 ms,
load 1697 ms · `/api/auth/session` **1518 ms** · first tRPC batch **1130 ms**. **The shell is fine; the map is
not.**

### Time-slider defects

Path: `LayerTimeSlider` → `useTimeSliderStore.setLayerDate` (clamped) → `layerDates[toggleId]` (sparse; absent =
follow that layer's `latestObservedDate`) → two consumers: (a) `useDebouncedLayerDay` (250 ms) → tRPC keys;
(b) `LayerManager.applyDateFilter` → `map.setFilter(id, ["<=", ["get","observed_day"], day])`.

**The date never reaches a Martin URL, by design.** `src/lib/map/sources.ts:75-86` builds
`${baseUrl}/${sourceIds.join(",")}` — no date, no query string. Tiles are identical for every date; the day is a
baked MVT attribute (`drizzle/0015_tile_observation_day.sql`) filtered client-side
(`src/lib/map/tile-layer-date-filter.ts:11-14`, "dragging across 1,400 days issues zero requests"). Correct
design — **conditional on the filter always being applied**, which is where D1 fails.

- **D1 CONFIRMED (highest value).** `src/components/map/LayerManager.tsx:583-586` gates `applyDateFilter` on
  `map.isStyleLoaded()`. The same file at `:540-549` documents why that gate is wrong — `isStyleLoaded()` requires
  *every* source's tiles to be in, so one unhealthy source holds it false indefinitely ("that is the outage of
  2026-08-15") — and states the visibility applier was moved onto `styledata` for exactly that reason, while
  `:548-549` explicitly excludes opacity and the date filter from that recovery path. Consequence: while any
  source is slow or 404ing, the only filter ever applied is the one written once in `onStyleLoad` (`:533`),
  typically `null`. **Symptom: dragging the slider changes nothing; the map draws the whole multi-year record
  under a row whose slider reads one day. Silent — no error, no caption.** Given the measurements above (a source
  routinely taking 84–117 s), `isStyleLoaded()` is false for minutes at a time on every session, so **D1 fires
  constantly.** **Stronger, per D0: until the 2026-08-17 CORS fix the Martin composite never loaded at all, so
  `isStyleLoaded()` was PERMANENTLY false and D1 fired 100% of the time in production — the date filter was never
  applied to any tile-baked layer.** The fix removes that standing trigger, not the defect. Fix: put
  `applyDateFilter` on the same `styledata` convergence path `applyVisibility` uses, throttled.
- **D2 CONFIRMED (fragility).** `tile-layer-date-filter.ts:44-53` passes any feature lacking `observed_day`.
  Correct per-row, but a whole layer whose tile function predates 0015 — or a Martin process not restarted after a
  tile migration (§5) — passes everything at every date, indistinguishable from D1 and from "no history".
  Nothing logs.
- **D3 CONFIRMED.** `src/hooks/useFireData.ts:71-92` — on 304 or on error, `data` is left untouched, and it is
  never reset when `date` changes (deps `[enabled, date, fetchFires]`, `:115`). Ordering *is* correctly guarded
  (monotonic `latestRequestRef` + `AbortController`, `:40-53`, `:87`), so this is unlabelled retention, **not** a
  race. Symptom: scrub to a past date; if that request fails, the previous day's detections stay painted while
  every caption says the new date.
- **D4 CONFIRMED.** `src/components/map/MapDateSummary.tsx:107,201` renders `layer.date` — the slider position,
  never the drawn collection's actual observed day. `src/stores/useMetricAtDate.ts:118-142` states the opposite
  rule ("consumers that assert a date to the user must read `resolvedDate`"); **no consumer does.** This is what
  makes staleness read as a data bug rather than a loading state.
- **D5 CONFIRMED.** The whole debounce / keep-previous / `resolvedDate` / prefetch layer is **dead code** — only
  `SCRUB_SETTLE_MS` is imported from `useMetricAtDate.ts`. Every live layer uses raw `trpc.*.useQuery` with **no
  `placeholderData: keepPreviousData`** (`LayerManager.tsx:230,239,243,256,334`;
  `useViewportProxiedLayers.ts:150,197,240`), each falling back to `EMPTY_FEATURE_COLLECTION` while pending.
  Symptom: **every date change and every pan drops the layer to zero features for a full round trip, then
  refills** — the blank-and-refill `useMetricAtDate.ts:196-199` was written to prevent. Reads to the user as both
  latency and staleness.
- **D6 CONFIRMED (latency).** `useViewportProxiedLayers.ts:77-95` + `viewport-bbox.ts:29` serialize bbox at **6
  decimal places** and pass raw float `zoom` into ~16 tRPC keys. A pan of ~10 cm mints a cold key for streamflow,
  groundwater, vegetation, weather, 3 soil fields, soil survey and 9 climate signals at once — each then blanks
  per D5. Mitigated only by `moveend` publishing (`MapView.tsx:147`), so it is per-gesture, not per-frame.
- **D7 CONFIRMED.** `layer-toggle-context.ts:320` sends `requestDate: undefined` at server-today, and
  `src/app/api/fires/route.ts:23,59` treats omitted as **the live FIRMS lookback window**, not today. Symptom:
  stepping the fire slider one day back collapses a multi-day window to one day — a large abrupt drop that looks
  like a data gap, at exactly the day users check first. Sound for warehouse readers where omitted == today;
  **unsound for `/api/fires`.**
- **D8 CONFIRMED.** Two 250 ms settle timers armed by different comparisons (`layer-toggle-context.ts:211-231`
  vs `:249-274`); the doc at `:203-210` admits one may lag a settle window. With D4, the caption visibly trails
  its layer during a drag.
- **D9 CONFIRMED (design cost).** `TimeSliderCapabilitiesLoader.tsx:8,71-76` polls every 5 min; `setCapabilities`
  re-clamps overrides (`time-slider-store.ts:472-479`) and re-resolves the sparse default to a possibly-new
  `latestObservedDate` (`:311-321`). Symptom: **the map appears to reload and jump a day on its own, mid-session,
  with no user action.**
- **D10 LOW.** `LayerManager.tsx:204-205` comment claims burn-severity gets no slider;
  `environmental-read-model.ts:2916` now sets `"burn-severity": "event"`, so it does. Misleads anyone debugging D1.
- **D11 CONFIRMED, NEW 2026-08-17, UNOWNED — the real cause of the fire layer's "abrupt drop".** Surfaced by the
  fires lane while fixing D7, and it **outlives that fix**. `/api/fires` resolves two genuinely different query
  *shapes*, not two date arguments: `{kind:"live"}` is a **rolling multi-day `createdAt` window** via
  `firmsDayRange()`, while `{kind:"historical"}` is a **strict single-day `observed_day` filter**
  (`resolveRequestedObservationDay` / `getPublishedFireDetections`, `environmental-read-model.ts:175-192`).
  Stepping the slider one day back therefore does not move a window — it **changes the kind of question being
  asked**, collapsing a multi-day window to one day. D7's fix canonicalised the *cache contract* around this and
  closed a latent CDN mismatch; it **cannot touch the shape asymmetry**, which lives in a file no lane owned.
  This is the defect actually responsible for the user-visible cliff at the day users check first.
- **D12 CONFIRMED and FIXED 2026-08-17 (found while fixing D3).** `useFireData`'s `etagCacheRef` cached only ETag
  *strings*. A 304 asserts the server's copy of **that date** is unchanged — it says nothing about what is
  currently painted in `data` if the slider visited another day and came back. So a 304 could repaint the
  **wrong day's** content. Left unfixed, D3's new staleness label would have been truthful-sounding on data that
  was still wrong — a worse failure than the silent retention it replaced. Fixed by caching the payload beside
  its ETag (`CachedFireResponse` / `responseCacheRef`, bounded at 60 with the `upsertBoundedFeature` LRU idiom).

### Cache and staleness defects

**Cache inventory, database → pixels:**

| # | layer | caches | TTL / invalidation | stale? | symptom |
|---|---|---|---|---|---|
| 1 | **IndexedDB persisted query cache** `plantgeo-query-cache` (`query-persister.ts:303`) | whole tRPC payloads for 9 allowlisted layer reads (`:68-81`), keyed by `queryHash` (path+bbox+date+depth+zoom) | **30 days** if `input.date < serverCurrentDate`, else 5 min (`:120-127`); LRU 50 MB (`:43`); or bump `CACHE_SCHEMA_VERSION` (`:34`) | **YES, 30 d, survives reload** | old value returns after a hard refresh |
| 2 | TanStack Query in-memory (`providers.tsx:12-22`) | everything | `staleTime` 60 s; `refetchOnWindowFocus:false`; per-query 24 h soil-survey, 1 h watersheds/soil-field/climate-field/vegetation/service-area, 15 min water/weather, 5 min metric | yes, min→24 h | toggling a panel off/on shows the old answer |
| 3 | server in-process capability memos (`environmental-read-model.ts:3701,3719,3751-3810`) | slider capabilities, `latestObservedDate` | 5 min feature / 30 min SWR signal; per-process, so N replicas disagree | yes, +30 min | a fresh day isn't offered for ~35 min |
| 4 | Redis GeoJSON (`redis.ts:110-133`) | `drought:current` 6 h, `drought:date:{date}` 24 h, `watersheds:{bbox}` 1 h, `mtbs:{bbox}:{y1}:{y2}` 24 h | `setex` only; no versioning, no purge verb | yes, 1–24 h | stale watershed/MTBS geometry |
| 5 | Next.js fetch cache (`bounded-upstream.ts:24-29`) | upstream providers | FIRMS 3600 s, hydrosheds 3600 s, MTBS 86400 s, LandFire 86400 s, GIBS 7 d/6 h | yes; **stacks under Redis** (watersheds 1 h + 1 h) | fires up to 1 h old before any other cache |
| 6 | HTTP/CDN (`api/fires/route.ts:16-18`, vegetation tile `:109`, mapillary `:89`) | public GETs | fires live `max-age=30,s-maxage=300,swr=600`; **fires historical `max-age=3600,s-maxage=86400,swr=86400`**; tRPC is POST → not CDN-cacheable | yes, **up to 48 h** on a past fire day | past fire day frozen at CDN |
| 7 | Martin tile cache (`martin.yaml:12-16`) | MVTs ≤ z14, 128 MB, `tile_expiry: 5m` | — | yes, 5 min + MapLibre session cache | the latency/payload tables above |
| 8 | **materialized views** (0029, 0023) | pre-aggregated census + rollups | `matview-refresh` pulse vs `agri.matview_refresh_state`; **4 fail every pulse**, 1 never called | **YES, unbounded** | see C3 |
| 9 | SoilGrids DB cell cache (`soilgrids.ts:62,252`) | per 0.001° cell | 90 d + deliberate serve-expired | by design (static ISRIC release) | negligible |
| 10 | nginx tile proxy (`nginx.conf:16-32`) | `/tiles/` | `proxy_cache_valid 200 1d` | **INACTIVE** (commented out of `docker-compose.yml:92-101`) | none today |

- **C1 CONFIRMED — every layer's default view gets the 30-day persisted TTL, not the 5-minute one.**
  `resolveCacheTtlMs` gives 30 days to any `date < serverCurrentDate` (`query-persister.ts:126`). But **no layer
  defaults to today** — each opens on its own `latestObservedDate` (`time-slider-store.ts:318-319`;
  `layer-toggle-context.ts:163-171`, comment `:295` "which for most layers is not today"). So the day the user
  sees on load is *always* past for vegetation, soil-field, climate-field, streamflow, groundwater and drought —
  written to IndexedDB with a **30-day** expiry and re-served for a month. **The staleness table above measures
  exactly this:** 11 layers open on a day 11–15 days old, so all 11 are pinned client-side for 30 days.
  The TTL rests on a premise that is **false for this warehouse**: `src/lib/cache/AGENTS.md:48-51` asserts "once a
  day is in the past, the warehouse's observations for it are immutable". The agri pipeline explicitly rewrites
  historical days — gap reopening (`sql/ingest/reopen_gap_windows.sql`), the 5-generation cap (`c14e36b`), and the
  open USDM/ERA5 self-healing lanes. **This is the #1 explanation for "comes back after a reload as an old
  value."**
- **C2 CONFIRMED — SWR revalidation writes IndexedDB but never the react-query cache.** `revalidateAgainstDW`
  (`query-persister.ts:251-293`) persists the fresh payload and returns it into a **floating promise**; the
  persister already returned `stored.value` at `:320` and nothing calls `queryClient.setQueryData`. Its own
  docstring at `:248-249` ("IndexedDB & react-query cache are updated") is **false**. Consequence: every
  allowlisted layer renders **exactly one fetch behind, permanently** — correct data appears only on the *next*
  mount of that exact key. Matches "doesn't update after changing controls; right on the second look." Secondary:
  revalidation fires on every fresh hit (`:319`), so the long TTL buys no network saving — only display latency.
  **Cheapest high-value fix in the whole pass: one `setQueryData` call.**
- **C3 CONFIRMED — failing / never-refreshed matviews, mapped to the surfaces they freeze.** The four failures
  are already recorded in §2; this is what each one costs the user:

  | matview | status | reader | frozen surface |
  |---|---|---|---|
  | `geo.mv_feature_observation_day` | **302 s vs 300 s cap — fails every pulse** | `environmental-read-model.ts:3386` (via `geo.v_observation_day_census`, `surface_kind='feature'`), `:1420`, `:4137` | **the time slider's available days + `latestObservedDate` for every `geo.features`-backed layer** (vegetation, fires, sensors, interventions, evacuation zones, burn severity) → the map opens on a frozen day and **cannot select newer ones**; also the vegetation "newest observation" caption and `getMetricAtDate`'s unlinked-count summary |
  | `geo.mv_signal_observation_day` | **301 s vs 300 s cap — fails every pulse** | `environmental-read-model.ts:3462-3466` (`surface_kind IN ('signal','polygon')`) | **slider axis for the 12 signal streams** — soil-moisture/soil-temperature field, climate field, streamflow, groundwater — plus `drought-areas` |
  | `geo.mv_soil_survey_grid` | fails, 66 s | **none** (`usda-soil.ts:1047,1148` not repointed) | no user-visible staleness; cost + red pulse only |
  | `geo.mv_soil_survey_union` | fails, 133 s — GEOS bug, `0029:918` omits `ST_CollectionExtract(...,3)` | **none** | no user-visible staleness |
  | `geo.watershed_rollup` | `geo.refresh_watershed_rollup()` called by **nothing** | `geo.watershed_tiles()` at `0023:201`, taken when `z<10` (`:149-155`) | **watersheds at z<10** draw an `observed_day` frozen at build time; **crossing z10 visibly changes vintage** |

  Net: the two census failures mean **no newly-ingested day is selectable in any slider**, which then feeds C1
  (frozen past `latestObservedDate` → 30-day client cache). **This is the causal spine linking the matview lane to
  the owner's staleness reports, and the live per-layer table above is its fingerprint.**
- **C4 CONFIRMED — Martin layers have no date dimension in the cache key because they have none in the URL.**
  Corroborates D1: nothing calls `setTiles` for them (only `VegetationLayer.tsx:364,375` and `SoilLayer.tsx:160`
  do, for rasters). Moving the slider **cannot** change a Martin tile; `tile_expiry` + MapLibre's session cache
  pin whatever drew first.
- **C5 CONFIRMED — `/api/fires` ETag is a feature *count*, not a content hash.** `api/fires/route.ts:60-61`:
  `W/"fire-${date ?? "live"}-${data.features.length}"`. Any corrective re-ingest preserving row count (geometry
  fix, confidence re-grade, replaced FIRMS batch) returns **304 forever**. With `HISTORICAL_DAY_CACHE_CONTROL`
  (`:18`) a past fire day is up to **48 h stale at the CDN**, with a 304 handshake that never breaks the tie.
- **C6 CONFIRMED — cache keys missing a user-changeable dimension.** `drought:current` (`services/drought.ts:4`)
  keys on **nothing** — no date, no bbox, 6 h; currently unreferenced (the router uses
  `getPublishedDroughtClassification`, `routers/environmental.ts:297-308`), so a **dormant landmine**.
  `watersheds:{bbox}` (`services/hydrosheds.ts:45`) has **no zoom**; safe only because `getWatersheds` is
  bbox-only today (`routers/environmental.ts:315`) — adding a zoom arg without touching the key immediately serves
  the wrong tier. **No Redis key carries a schema/version prefix**, so a response-shape change is served in the old
  shape for a full TTL after deploy.
- **C7 CONFIRMED — stream capabilities are SWR with no staleness ceiling.** `readStreamCapabilities`
  (`environmental-read-model.ts:3789-3810`) serves the cached value while a refresh runs and replaces it only on
  success. With `mv_signal_observation_day` failing every pulse, the 30-minute TTL (`:3719`) is irrelevant —
  refreshed on schedule with permanently frozen content. The comment at `:3717-3719` justifies 30 min by the
  "6 h min / 24 h max staleness in `agri.matview_refresh_state`" — **exactly the assumption the failing lane
  breaks.**
- **C8 PLAUSIBLE, dormant — nginx tile TTL overrides are dead regexes.** `nginx.conf:18` is
  `~^\tiles\(?:pmtiles|fonts|sprites)` — literal backslash, no leading `/`, can never match; `:19`'s
  `~^/tiles/geo\.` cannot match either (Martin source ids are bare, `martin.yaml:25-26`). Everything would fall to
  `default 86400` — 24 h browser cache on *dynamic* MVTs. Not active; a trap for whoever re-enables it.
- **C9 NOTE — `serverCurrentDate` is NOT compromised.** Derived from the server clock
  (`environmental-read-model.ts:3118`) and stamped fresh on every capabilities call outside the memo
  (`:3879-3883`). The frozen census does not corrupt "today"; C1 is reached through `latestObservedDate`, not
  through a bad today.

### Why the map feels stale, slow and wrong

Four independent causes, **each sufficient on its own** — which is why single fixes have not helped. **Above all
four sat D0: the Martin CORS block, which made every dynamic layer blank outright until it was fixed 2026-08-17.**

1. **The census matviews that gate the sliders fail every pulse** (C3) → no new day is selectable → every layer
   opens on a past day → **the 30-day IndexedDB TTL pins it for a month** (C1). Live fingerprint: 11 layers
   11–15 days stale.
2. **The persisted-cache revalidation never reaches react-query** (C2) → every allowlisted layer is permanently
   one fetch behind. One-line fix.
3. **The date filter is gated behind `isStyleLoaded()`** (D1) → and because a tile source routinely takes
   84–117 s, `isStyleLoaded()` is false for minutes on every session → **the slider silently does nothing** for
   the tile-baked layers. **Under D0 it was worse: the composite never loaded, so this fired continuously.**
4. **The tile path scans a 7.2 GB table through a shared 1.3M-entry GiST tree and serializes unsimplified
   geometry** → 117 s tiles and 10.3 MB payloads → pool saturation, blank map, and the RAM pressure the owner
   named. **§10 is the structural answer.**

D4 (captions assert the slider's day, not the drawn day) and D5 (no `keepPreviousData`, blank-and-refill) are what
turn all of the above from "slow" into "wrong and untrustworthy".

**Cheapest-first fix order:**

1. **C2** — one `setQueryData` in `revalidateAgainstDW`. Minutes.
2. **D1** — move `applyDateFilter` onto the `styledata` convergence path, throttled. Small.
3. **D5** — add `placeholderData: keepPreviousData` to the layer queries. Small, kills the flicker.
4. **D4** — caption from `resolvedDate`, not the slider day. Small, restores trust.
5. **C1** — TTL policy relative to `latestObservedDate`, or a `dataRevision` gate. Design needed.
6. **C3** — the two census matviews (index or re-grain so they finish under the 300 s cap).
7. **D6** — quantize bbox/zoom in the tRPC keys.
8. **C5** — content-hash the fires ETag.
9. **§10** — the join-free relation layer. Largest, and the only one that fixes the latency/payload/RAM axis.

### Correct behaviour that must NOT be "fixed"

1. `sensors`, `evacuation-zones`, `interventions`, `watersheds` draw their whole record at every date and have no
   slider — declared `snapshot` (`environmental-read-model.ts:2917-2922`); `LayerManager.tsx:423-428` passes
   `null`, which *clears* the filter. Reasoned at `:201-210`: filtering them on `latestObservedDate` was erasing
   sensors observed on a partially-ingested live-edge day.
2. `soil-survey` likewise takes `DEFAULT_TEMPORAL_KIND = "snapshot"` (`environmental-read-model.ts:2944`) —
   consistent, though by table omission rather than decision.
3. Vegetation's NDVI raster does not change when scrubbing inside a month — GIBS is a monthly composite
   (`layer-toggle-context.ts:530-531`, `VegetationLayer.tsx:348,362-375,407`). The vegetation GeoJSON overlay
   *does* move per day. **Half the layer changing is correct.**
4. `soil` never draws — permanently withheld with a stated reason. (The trace cites
   `layer-registry.ts:295-296`; §5's trap entry cites `:289-290` for the same block — line drift, same code, and
   §5's separate finding that the `permanentlyUnavailableReason` is **stale and wrong** still stands.)
5. `demand-heatmap` / `strategy-recommendations` have no time control by declaration
   (`layer-registry.ts:390,423`).
6. **The MapLibre v5 `setStyle` gotcha is NOT in this path.** `setStyle` is called only on basemap change
   (`MapView.tsx:217-241`) with `once("style.load", …)` registered *before* it (`:233-234`), exactly as the gotcha
   requires. No date change calls `setStyle`; no layer remounts on a date change.
7. **Out-of-order response clobbering is structurally absent.** React-query keys by date and cannot write a stale
   key into a fresh one; the one hand-rolled fetch carries a monotonic id and an `AbortController`. The staleness
   is D3/D4/D5, **not** a race.
8. **"Most layers empty before 2026-08-02" is NO LONGER a valid excuse for emptiness.** Since per-layer dates,
   each row opens on its own `latestObservedDate` (`time-slider-store.ts:311-321`) and `setLayerDate` clamps to
   that layer's own `firstDay` (`:434-448`) — a layer *cannot be scrubbed* before its own record starts. A layer
   rendering empty inside its own drawn track is a **real defect**. **This supersedes the
   `plantgeo-layer-history-depth` caveat as a triage shortcut.**

Also not a defect: a blank map in a non-active Chrome tab is the rAF-suspension harness artefact (§5), and
`document.hasFocus()` is **not** sufficient to rule it out.

---

## 10. Join-free tile serving relations (PROPOSAL, not approved)

**Nothing here is implemented and nothing here is approved.** This answers the owner's directive — *"there is a
fundamental issue with RAM — we need to be more able to easily and effectively query pre-aggregated views,
returning everything we need with a simple query, with no joins if possible"* — as a design for review.

**Blocked on three owner decisions** (see the ordered path below): **(a)** keep or skip `drizzle/0030`;
**(b)** the fire-detail time cap `N`, or "no cap"; **(c)** whether `tile_interventions` gains a day column — it
has none today, and adding one changes what the slider does to that layer.

### Current shape

Seven live `geo.*_tiles()` functions (latest `CREATE OR REPLACE` wins):

| function | authoritative file:line | source | join | layer resolved by | day emitted |
|---|---|---|---|---|---|
| `fire_risk_tiles` | `0015_tile_observation_day.sql:159` | `geo.features` | `JOIN geo.layers` | `l.name='fire-perimeters'` | yes |
| `sensor_tiles` | `0015:198` | `geo.features` | `JOIN geo.layers` | `l.name='sensors'` | yes |
| `evacuation_zone_tiles` | `0015:116` | `geo.features` | `JOIN geo.layers` | `l.name='evacuation-zones'` | yes |
| `burn_severity_tiles` | `0015:63` | `geo.features` | `JOIN geo.layers` | `l.name='burn-severity'` | yes |
| `intervention_tiles` | `0005_intervention_priority_tiles.sql:6` | `geo.features` | `JOIN geo.layers` | `l.name='interventions'` | **no** |
| `watershed_tiles` | `0023:131` | `geo.features` (z≥10) / `geo.watershed_rollup` (z<10) | join **only on z≥10** | `l.name='watersheds'` | yes |
| `strategy_recommendations_tiles` | `0028:360` | 3 matviews by zoom | **none** | n/a | no |
| `building_tiles` | `0001:485` | `geo.osm_buildings` | none | n/a | no |

The five joined functions are byte-identical in shape (canonical `0015:101-108`): `FROM geo.features f JOIN
geo.layers l ON f.layer_id = l.id WHERE l.name = '<layer>' AND l.is_public IS TRUE AND f.status = 'published' AND
f.geom IS NOT NULL AND f.geom && bounds_4326 AND ST_Intersects(f.geom, bounds_4326)`. Each then does **7–10
`f.properties ->> '<key>'` jsonb extractions per row** against a 1,467 MB TOAST relation (`0015:83-100`), plus a
per-row `ST_Transform(f.geom, 3857)` (`0015:82`) while bounds are transformed the other way (`0015:76`) — **two
reprojections per tile row, both avoidable.**

**Fire-detections is not a tile function at all** — `layer-registry.ts:178-187` marks it `renderKind:"component"`,
served as GeoJSON by `environmental-read-model.ts:317-339`. Its 3,009,567 rows harm the tile path *indirectly*, by
owning ~1.3M entries in the shared `idx_features_geom` tree every tile function's `&&` leg must walk.

### The join claim — proved, with one important correction

`geo.layers` (`0000_narrow_tony_stark.sql:133-147`) could supply `name`, `is_public`, `style`, `min_zoom`,
`max_zoom`, `sort_order`. Grep across all seven bodies: **zero references to `l.style`, `l.min_zoom`, `l.max_zoom`,
`l.type`, `l.sort_order`.** Zoom tiering is hardcoded in the bodies (`0023:149-155`, `0028:376,393`), not read from
`layers`. So the join carries exactly **name→id resolution plus a publication boolean** — two constants a per-layer
relation eliminates by construction. This does **not** revive per-layer partial indexes: §3's rejection stands, and
for the same reason (only `l.name` is constant; equivalence classes propagate `f.layer_id = l.id` but never the
restriction on `l.name`).

**Correction that matters: the join is NOT the reason the query is slow.** The layer leg is 0.8 ms for all 541 rows
(`0030` header EXPLAIN). The 45.6 s is the **global geometry leg**. Removing the join is necessary for the relation
design but is not itself the win — **the win is that a per-layer relation's GiST tree contains only that layer's
geometries**, shrinking the expensive leg by 3–4 orders of magnitude.

### Proposed relations

Grain rule: **one row per feature per observation day, geometry pre-projected to 3857, every wire attribute a
native typed column, zero jsonb, zero join.** Schema `geo`, prefix `tile_` to distinguish from 0029's analytics
`mv_`.

| relation | grain | rows | extra columns (all carry `geom_3857`, `observed_day`) | est. size | day? |
|---|---|---|---|---|---|
| `geo.tile_sensors` | 1/feature | 149,466 | `id`, `network`, `sensor_id`, `station_name`, `observed_at` | 25–45 MB | yes |
| `geo.tile_watersheds_detail` | 1/HUC12 | 9,396 | `id`, `huc12`, `huc_level`, `basin_count`, `name`, `areasqkm`, `tohuc`, `states`, `hutype` | 150–400 MB (**unmeasured**) | yes |
| `geo.watershed_rollup` | existing | 2,162 | unchanged | existing | yes |
| `geo.tile_burn_severity` | 1/perimeter | 541 | `id`, `fire_id`, `fire_name`, `fire_year`, `ignition_date`, `fire_type`, `assessment_type`, `acres`, `severity_class`, `observed_at` | 30–120 MB (**unmeasured**) | yes |
| `geo.tile_fire_perimeters` | 1/perimeter | **uncensused** | `id`, `risk_level`, `severity`, `name` | unknown | yes |
| `geo.tile_evacuation_zones` | 1/zone | **uncensused** | `id`, `evacuation_area_name`, `fire_name`, `county`, `severity`, `evacuation_level_label`, `structures_within`, `population_within` | small | yes |
| `geo.tile_interventions` | 1/feature | 2 | `id`, `intervention_type`, `priority`, `status`, `name`, `description` | 16 kB | **no** — emits none today; do not invent one |
| `geo.tile_fire_detections_detail` | 1/detection | 3,009,567 | `id`, `brightness`, `frp`, `confidence`, `acq_time`, `satellite` | ~300 MB + ~200 MB GiST | yes |
| `geo.tile_fire_detections_z9` | 0.005° cell × day | ~1.2–2.4M (**unmeasured**) | `cell_id`, `detection_count`, `max_frp`, `max_confidence` | ~150 MB | yes |
| `geo.tile_fire_detections_z6` | 0.02° cell × day | ~0.4–1.0M | same | ~60 MB | yes |
| `geo.tile_fire_detections_z0` | 0.1° cell × day | ~0.15–0.5M | same | ~30 MB | yes |

**Type: materialized view** — the only shape that plugs into the existing refresh machinery with no new writer
(0029's header argues the case at `:26-37`). Not partitioned: everything except the fire tiers is under 150 K rows,
and the fire tiers are already partitioned *by zoom*, which is the access pattern.

**Exactly two indexes each:** `CREATE UNIQUE INDEX uq_tile_<layer> ... (id)` (required by `REFRESH CONCURRENTLY`)
and `CREATE INDEX ix_tile_<layer>_geom ... USING gist (geom_3857)`. Fire tiers key on `(cell_id, observed_day)`.

**The day dimension stays in every relation that emits it today — non-negotiable.**
`tile-layer-date-filter.ts:17-23` names four toggles (`fire-perimeters`, `evacuation-zones`, `burn-severity`,
`sensors`) whose slider *is* a MapLibre expression over the `observed_day` MVT attribute. Drop it and the slider
silently shows everything at every date — the exact regression 0015 fixed. Materialize as
`geo.feature_observation_day(properties)::text`, unchanged, so `observation-day-contract.test.ts` keeps holding.

**Fire-detections has a different day rule that must NOT be unified.** `environmental-read-model.ts:309-315`
states `geo.feature_observation_day` does not know `acqDate` and returns NULL for most detections. Fire relations
carry `COALESCE(substring(properties->>'observedAt',1,10), properties->>'acqDate')::date` — a deliberate second
rule. Fire is also an **event** layer (`environmental-read-model.ts:2905`), so its client semantics are "on that
day", not the "at or before" of `tileLayerDateFilter`; a fire tile function needs its own filter expression.

**Zoom tiering follows 0023's precedent** (detail tier reads the base relation, coarser tiers read a pre-unioned
rollup keyed by a real published hierarchy, `CASE` on `z` at `0023:149-155`, dispatch `:157/:185`):

- *Polygons* — watersheds keep 0023's shape (only the z≥10 branch changes source). Burn-severity and evacuation
  zones get **simplification, not rollup**: a second `geom_3857_z6 = ST_SimplifyPreserveTopology(...)` column
  selected by zoom. There is **no published hierarchy** to roll up by, so unioning would fabricate groupings —
  which 0023 explicitly refused to do.
- *Points (fire-detections)* — rollup by **grid cell × day** (`ST_SnapToGrid` 0.1°/0.02°/0.005°, `count(*)`,
  `max(frp)`). A 0.1° cell at z4 is sub-pixel, so the aggregate is visually lossless. **Keep `observed_day` in
  every tier** — coarsening to month would make the slider a lie.
- The fire **detail** tier is the one relation to cap by time (`WHERE observed_day >= current_date - N`), matching
  the live path which already reads only `firmsDayRange()` (`environmental-read-model.ts:355`). This is a real
  semantic narrowing and **needs owner decision (b) on `N`.**

**This design targets §9's payload problem, not only latency:** `sensor_tiles` alone returns 2.1 MB at z6 today
because 149,466 unclustered points are serialized whole. Simplification and grid rollup are what bring a 10.3 MB z5
tile down to something a browser can decode without stalling.

### Refresh registration — no new mechanism

Register each as one more `MatviewRefreshSpec` in `jobs/matview_refresh.py:220-320`. The lane already opens one
never-rotating run, gates on a watermark, self-heals unpopulated views with a non-concurrent first `REFRESH`, and
writes `agri.matview_refresh_state`.

| relation | watermark | min_interval / max_staleness | priority | stmt timeout |
|---|---|---|---|---|
| `tile_interventions`, `tile_evacuation_zones`, `tile_burn_severity`, `tile_fire_perimeters` | reuse `_WATERMARK_FEATURES_UPDATED_AT` | 900 / 21,600 | 0 | 60 |
| `tile_sensors` | reuse `_WATERMARK_FEATURES_UPDATED_AT` | 900 / 21,600 | 1 | 120 |
| `tile_watersheds_detail` | reuse `_WATERMARK_WATERSHED_FEATURES` | 86,400 / 604,800 | 1 | 300 |
| `tile_fire_detections_*` (4) | **new** `matview_refresh_watermark_fire_detections.sql` | 3,600 / 21,600 | 2 | 600–1,800 |

**Caveats:** adding 11 specs to an 11-spec lane doubles per-tick watermark probes, and the lane is **already
blowing its 300 s cap on two views** (§2). Fire detail belongs at `priority=2` beside `mv_signal_cell_daily` so it
cannot starve the cheap ones. Every relation needs its unique index or `REFRESH CONCURRENTLY` refuses
(`0029:34-37`).

**Free bug fix:** watersheds' `geo.refresh_watershed_rollup()` is called by nothing today (§5, §9 C3); the rollup
is already adopted into this lane, and the new detail relation joins it under the same discipline.

### Expected win, and the honest unknowns

**Structural, knowable now:** every tile query stops touching `geo.features` (7,219 MB), `idx_features_geom`
(310 MB) and the 1,467 MB TOAST relation. The 1,318,892-entry GiST walk **cannot occur** — `tile_burn_severity`'s
tree holds 541 entries, `tile_interventions`' holds 2. The 7–10 jsonb detoasts per row per tile become native
column reads (**the single largest RAM working-set reduction, and a certainty**). Both per-tile reprojections
disappear. Tile-path resident working set drops from "whatever fraction of a 7.2 GB table the envelopes touch" to
**~250–600 MB** for the five style-baked layers, or ~1.0–1.5 GB with the full fire detail tier. On a 3 GB cap that
is the difference between a burst that hits the ceiling and one that does not.

**Latency: single-digit-to-low-hundreds of milliseconds per tile** expected (small GiST descent +
`ST_AsMVTGeom` on ≤10 K features, capped by `max_feature_count: 10000` in `martin.yaml:19`), against the
**117 s / 84 s measured in §9. No tighter number is claimed.**

**Cannot be known without prod EXPLAIN / census** (recorded as a standing assumption in §4): on-disk size of every
polygon relation (vertex counts unmeasured — 150–400 MB and 30–120 MB are order-of-magnitude only); row counts for
`fire-perimeters` and `evacuation-zones` (**never censused**); fire grid-tier row counts (could be 5× either way);
refresh duration per relation, hence whether the lane's budget absorbs 11 more specs; whether the planner actually
picks the new GiST index (it should — small relation, `&&` on the leading and only column — but that is exactly
deferred finding MEDIUM #4, "asserted, not demonstrated").

**Relationship to 0030, stated plainly:** this design makes `ix_features_layer_geom` unnecessary *for the tile
path*, the only consumer with a measured EXPLAIN. It would still help `readFireDetectionsOnDay`'s bbox branch.
Since 0030 is authored, needs no code change, no Martin restart, and 25–60 minutes, **the recommendation is to land
it first as the de-risking fix** (which is what §7 step 2 already says) and treat this design as the structural
replacement — but if both land, 0030's index becomes a 400–600 MB carrying cost with one thin reader. **That is
owner decision (a) and it should be made BEFORE the 25–60 minute build, not after.**

### Migration path and ordering traps

**Load-bearing property: every tile function keeps its exact name, argument list and return type.**
`CREATE OR REPLACE FUNCTION geo.<name>(integer,integer,integer) RETURNS bytea` with a new body is **no catalog
change and no Martin restart** — 0015's header states this at `:7-10` and 0015 shipped on that basis.

1. **Never rename a tile function, never add/remove one in the same change.** `sources.ts:33-40` composes six ids
   into ONE MapLibre source and `:28-32` states a 404 on any member fails the whole TileJSON and **blanks every
   dynamic layer**. A rename is a 404 for the window between SQL landing and Martin restarting.
2. **Never change an MVT tag or attribute name.** `ST_AsMVT(tile,'burn_severity',...)` and `'watersheds'` are
   declared verbatim in `src/lib/map/layers.ts`; `0023:121-123` records a mismatch "renders nothing while
   reporting no error". `observed_day` must keep its name and `::text` YYYY-MM-DD form or
   `tile-layer-date-filter.ts` silently stops filtering (§9 D2).
3. A *new* function is only ever safe in this order: create in SQL → add to `infra/martin/martin.yaml` →
   **redeploy `plantgeo-martin`** (`auto_publish:false`, catalogue read at startup) → confirm it answers → *then*
   add its id to `DYNAMIC_TILE_SOURCE_IDS`. **This design needs none of that.**

**Ordered steps:**

1. **Owner decisions first, before any DDL:** (a) keep or skip 0030; (b) the fire-detail cap `N`, or "no cap";
   (c) whether `tile_interventions` gains a day column.
2. **Census `fire-perimeters` and `evacuation-zones` and measure real polygon byte sizes**, so the size table
   stops being a guess. Read-only, cheap.
3. **`0031_tile_serving_relations.sql`** — create every matview **`WITH NO DATA`** plus unique + GiST indexes.
   `WITH NO DATA` is mandatory (0029 header `:26-32`): creating with data runs every defining query inside the
   migration's single transaction during `preDeployCommand`, on the box this whole workstream exists to protect.
   **No `CONCURRENTLY` anywhere in the file** (25001 inside the migrator's transaction; `IF NOT EXISTS` does not
   save you). **Never write the drizzle statement-breakpoint literal inside a comment** — see §5; this bit 0029.
4. **Populate out of band**, session-tuned, in the style of `scripts/apply-pre-aggregation.mjs` — but **do not copy
   its `lock_timeout = '5s'`** (§5: that hardcoded value left an INVALID index that had to be dropped and rebuilt
   at 20 min).
5. **Verify populated and non-empty per relation before any function is touched.** A tile function pointed at an
   unpopulated matview returns empty tiles — not an error, a silently blank layer. The known `to_regclass` gap
   applies (§4): existence is not usability, and it false-passes on a matview created `WITH NO DATA`. Check
   `pg_class.relispopulated` **and** a real `count(*)`.
6. **`0032_tile_functions_read_preaggregates.sql`** — `CREATE OR REPLACE` the six bodies, same signatures, same
   MVT tags, same attribute names and order. Apply to prod.
7. **Restart `plantgeo-martin` anyway.** Not strictly required (signatures unchanged), but bodies changed and
   Martin caches tiles for 5 min; a restart is the deterministic way to drop stale bytes, and the standing rule is
   "restart after a tile migration".
8. **Register the specs** in `jobs/matview_refresh.py` + the new fire watermark SQL. Code, not DDL; ships on the
   ordinary deploy.
9. **Only after 0031 and 0032 are both live in prod**, commit the two journal entries **and** the
   `migration-contract.ts` re-pin **in one commit** (§5, "Migration ordering"). Landing that pair early 503s
   `/api/ready` and fails the Railway healthcheck. Note `migration-contract.ts:2` is currently pinned to
   `0029_pre_aggregation_layer` and **0030 is not journalled** — if 0030 lands first that is its own separate
   one-commit pair, ahead of this one.
10. The journal legitimately skips 26 — do not "repair" it. Next free indices: 30 (taken by unapplied 0030), then
    31, 32.

**Rollback:** every step before 6 is purely additive and reversible by dropping the matviews. Step 6 is reversible
by re-applying the 0015/0005/0023 bodies via `CREATE OR REPLACE` — again no rename, again no composite 404.

---

## 11. Offline sync-on-scrub — owner spec, 2026-08-17 (APPROVED, not built)

Owner directive, verbatim: *"it should sync what has been selected previously by the user, by default that's the
most recent day, but when the slider slides there is a debounce and a fetch lag (loading state needed) and then
the data for that day is fetched where it should then be also persisted at which point on top of the time slider
there should be another line that fills in according to what days have been synced to local"* · plus *"It should
rely on what has been locally synced first"* and *"The sync by the user should be rate limited for all the time
streams using a uniform convention for all layers."*

**Read this before designing anything: there is NO bulk "Sync" button.** Sync is a **byproduct of ordinary
scrubbing**. The user browses; each day they land on is fetched, persisted, and then lights up on a second track
above the slider. This is materially simpler than a range-picker or a prefetch-everything button, and it is the
approved shape. Do not reintroduce a bulk-sync affordance.

### 11.1 The loop, end to end

1. Slider scrubs → **debounce** (`SCRUB_SETTLE_MS = 250`, `stores/useMetricAtDate.ts:34`, already shared by
   `layer-toggle-context.ts:223,266` so no two consumers double-fire).
2. Settled day issues the layer's fetch → **a real loading state must be visible for the fetch lag.**
   **This is defect D5 (§9) stated as a feature request.** Today every layer falls back to
   `EMPTY_FEATURE_COLLECTION` while pending, so the layer *blanks and refills* with no indication it is loading.
   `placeholderData: keepPreviousData` plus an explicit pending indicator is the fix; they are one change.
3. Result persists to IndexedDB — **already happens** for the 9 allowlisted tRPC paths via
   `lib/cache/query-persister.ts:303-363`. Nothing new is needed for the *write*; what is missing is the *index*
   (11.2).
4. The **synced-days track** above the slider fills in for that day.

Default day is already correct: `layerDates` is sparse and an absent key means "follow this layer's
`latestObservedDate`" (`time-slider-store.ts:311-321`, `resolveLayerDate`). No change.

### 11.2 The blocker nobody can design around: entries are not enumerable by layer

`StoredLayerQueryEntry` (`query-persister.ts:46-58`) carries
`{key, schemaVersion, createdAt, expiresAt, lastAccessedAt, approxByteSize, value, etag?, dataRevision?}` —
**no `layerId`, no `day`.** The IDB key is the opaque tRPC `queryHash`, which is **not reversible**. And
`indexeddb-store.ts:22-40` opens the store with **no `keyPath` and no `createIndex`** — it is a bare
out-of-line key→value store with no secondary index of any kind.

**Consequence: "which days does layer X have on disk" is unanswerable today.** Both the coloured track and the
per-timeline reset depend on it. This is the first thing to build.

**Shape:** stamp `layerId` (via `routerPathFromQueryKey` → `toggleIdForWarehouseLayerName`,
`layer-registry.ts:480-485`) and the `day` (already in `queryInputRecord(queryKey).date`) onto the entry at
**both** write sites (`:284` revalidation, `:356` cold write), then enumerate with the existing
`getAllEntries` (`indexeddb-store.ts:105-108`). Bump `CACHE_SCHEMA_VERSION` (`:34`) so pre-existing entries
without the stamp are treated as misses rather than as "not synced" lies.

**Trap:** `getAllEntries` is a full scan of a 50 MB store. Do not call it per render. Read it once into a
zustand store on mount and mutate that store on every persister write.

### 11.3 The synced-days track — where it goes

`LayerTimeSlider.tsx` already renders exactly this shape, so **do not invent a new rendering approach.** The
track is a stack of absolutely-positioned `<div>` bands under one transparent `<input type="range">`:

- dense base band `:471-479` · future hatch `:482-493` · **coverage bands `bands.map(...)` `:501-518`** ·
  latest tick `:522-531` · today tick `:533-538` · the range input `:550-565`.
- Bands come from `drawCoverageBands(domain, capability)` (`:253-256`) in
  `layer-panel/layer-coverage-track.ts`; kinds are the `CoverageKind` union (`:39`) styled by
  `TRACK_REGION_APPEARANCE` (`:356-381`).

The owner asked for a **second line on top of** the existing track — so this is a **sibling row**, not a new
`CoverageKind` blended into the existing band run. Render it as its own thin absolutely-positioned strip above
the range input, reusing `percentOfDayOffset` for geometry so the two lines register pixel-exactly.

### 11.4 Per-timeline reset

New control. Home is the controls stack in `LayerRow.tsx:187-202` (which already hosts `LayerOpacitySlider` and
the `LayerTimeSlider` slot), or the slider's own top row `LayerTimeSlider.tsx:404-456` beside the existing
"Latest" button. Both are legitimate; the top row is the tighter fit because that row already carries the date
input, the "NOT LATEST" badge and an action button.

**There is no precedent to copy.** The only existing "clear" affordance is `OfflinePanel.tsx`'s "Clear Cache" →
`clearTileCache()` (`lib/offline/tile-cache.ts:156-164`), which deletes **only** CacheStorage keys prefixed
`plantgeo-` and touches none of the query cache.

### 11.5 The uniform rate limit — a real consolidation, not a wrapper

Owner: *"uniform convention for all layers."* Today the client has **six independent, differently-shaped
request-shaping mechanisms and no shared limiter**:

| mechanism | file:line | governs |
|---|---|---|
| `MAX_CONCURRENT_REVALIDATIONS = 2` hand-rolled semaphore | `query-persister.ts:213`, `:225-244` | SWR revalidation after an IDB hit, allowlisted layers only |
| `SCRUB_SETTLE_MS = 250` | `useMetricAtDate.ts:34` | slider scrub → request |
| `useDebounce(value, 300)` | `hooks/useDebounce.ts:3` | generic, used beyond the slider |
| `PREFETCH_RADIUS_DAYS = 1` | `useMetricAtDate.ts:54` | neighbour-day prefetch volume |
| reconnect backoff 1 s→30 s | `useSSE.ts:16-77`, `useWebSocket.ts:17-86` | reconnect only, not requests |
| MapLibre paint throttle | `ui/layer-opacity-slider.tsx:21` | **render, not network — do not conflate** |

**And two confirmed-unthrottled user-triggered paths:** `useOfflineSync.runSync()`'s plain sequential
`for (const op of ops)` replay (`hooks/useOfflineSync.ts:80`) with zero cap, zero delay, zero backoff; and
`prefetchTiles`' no-service-worker fallback (`lib/offline/tile-cache.ts:80-104`) which fires `fetch()` for every
tile URL in a batch with no concurrency cap.

**Server side there is nothing to lean on.** `trpc/init.ts:18` — `publicProcedure = t.procedure`, **zero
middleware**; the four procedure builders add only auth/role checks. The `environmental`, `wildfire` and
`layers` routers that back every map layer have **no rate limiting at all.** The two Redis limiters that do
exist (`security/public-provider-rate-limit.ts:36-64`, `middleware/api-auth.ts:262-289`) cover only the
anonymous provider proxies and `/api/v1/*` API-key routes, and they have **different failure semantics**
(one fails closed only in prod, the other always). Do not extend either; write one client-side limiter.

**The limiter must cover the fan-out, which is the actual problem.** One settled scrub with the usual layers on
mints ~16 tRPC keys at once — 9 climate signals each with their own `useDebouncedLayerDay` + query
(`ClimateFieldLayers.tsx:40-59`, `:82`, `:86-92`), 3 soil fields, vegetation, weather, streamflow, groundwater,
drought. Combined with D6 (6-decimal bbox in the key, `viewport-bbox.ts:29`) a 10 cm pan mints all of them cold.

**`/api/fires` is structurally outside all of it.** `useFireData.ts:66-69` is a raw `fetch`, not react-query, so
the persister can never see it and it is not covered by the allowlist as designed. It needs explicit handling in
both the sync index and the limiter, or fire silently behaves differently from every other layer.

### 11.6 Satellite first — status corrected

Owner: *"ensure the satellite views are shown first for the visualization."*

- **`HEAD` still opens on `dark`.** `git show HEAD:src/stores/map-store.ts` → `currentStyle: "dark"` at `:81`.
  The working tree changes it to `"satellite"` (`map-store.ts:98`) but **that change has never been committed or
  deployed**, so production has never once opened on satellite. §8's "default basemap is now satellite" describes
  the working tree, not prod.
- The style itself is complete and correct: `satelliteStyle` (`lib/map/styles.ts:271-320`) uses Esri World
  Imagery, `layers[]` ordered `satellite-base` → hillshade → 3D buildings → dynamic layers → labels. Basemap is
  correctly at the bottom; nothing to reorder.
- **The choice does not survive a reload.** `map-store.ts` has **no `persist` middleware** (only `layer-store.ts`
  and `search-store.ts` persist, both to localStorage). Every reload resets to the default, so "shown first"
  requires the committed default *and* — if the user's own pick should stick — persisting `currentStyle`.
- **Standing risk, unowned:** satellite is `server.arcgisonline.com`, per `app/about/page.tsx:270` *"the one tile
  service in the stack we do not host"*. Making it the default makes an unkeyed third-party endpoint load-bearing
  for first paint. Flagged, not blocking.

### 11.7 The service worker is HEALTHY — a claimed defect, REFUTED 2026-08-17

**This section previously claimed the service worker was probably dead. That was WRONG and is corrected here.
Recorded in full because it is a reusable lesson about how the claim was manufactured.**

**The claim:** `public/sw.js:7-10` precaches `APP_SHELL` including `/manifest.webmanifest`; no such file exists
under `public/`; `cache.addAll` (`:39-45`) rejects wholesale on any 404; therefore `install` rejects and the
browser discards the worker.

**Why it was wrong: `src/app/manifest.ts` exists.** It is a Next.js App Router `MetadataRoute.Manifest`
**file-convention route**, which Next serves at exactly `/manifest.webmanifest` and for which it **auto-injects
`<link rel="manifest">`**. A glob of `public/**` was never going to find it. The premise came from enumerating
the filesystem and inferring the served reality — those are not the same thing.

**Measured against production 2026-08-17, in a real browser at `https://plantgeo.aevani.com`:**

- `fetch('/manifest.webmanifest')` → **HTTP 200**.
- `document.querySelector('link[rel="manifest"]').href` → `https://plantgeo.aevani.com/manifest.webmanifest`.
- `navigator.serviceWorker.getRegistration()` → scope `/`, **`active: "activated"`**, installing `null`,
  waiting `null`.
- `navigator.serviceWorker.controller` → **CONTROLLING**.
- `caches.keys()` → `["plantgeo-v2"]` — **the cache exists, so `install` and `cache.addAll` both succeeded.**

Corroborated independently from build artifacts: `.next/server/app/manifest.webmanifest.{body,meta}` exists with
`{"status":200,"content-type":"application/manifest+json"}`, and the same pair exists under
`.next/standalone/...`, which `output: "standalone"` (`next.config.ts:18`) copies into the Railway runtime image.
`APP_SHELL`'s literal `/manifest.webmanifest` matches the served path exactly — no basePath, no trailing-slash
drift, and `src/middleware.ts` matches only `/dashboard` and `/onboarding`.

**Two further claims in the original finding are also false:** the two icons are **not** "referenced by nothing"
(`src/app/manifest.ts:13,15` reference both), and there is **no missing `<link rel="manifest">`** to add to
`layout.tsx` — Next injects it.

**Do NOT create `public/manifest.webmanifest`.** A static file at that path would be served in preference to the
generated route, **silently shadowing** `src/app/manifest.ts` and turning a non-bug into a real one.

**One genuine latent hazard survives, unfixed on purpose.** `cache.addAll(APP_SHELL)` is still all-or-nothing, so
a *future* 404 in `APP_SHELL` would still kill the install. It is **not firing today**. The minimal fix is
per-entry `cache.add(url).catch(...)` or `Promise.allSettled`, which needs **no `CACHE_NAME` bump** (cached
content shape is unchanged) and has no happy-path behaviour change. Deliberately deferred: `public/sw.js` has
**zero test coverage**, and it is live-controlling real production clients. It deserves its own pass with a
harness, not a same-session unverified edit to service-worker lifecycle code.

**The lesson, which is the reusable part:** a filesystem glob is not evidence about what a URL serves, and this
is the second time in two days that a confident code-trace finding died on contact with a real browser (the
first being D0's misleading "expired Protomaps pin" error message). **Framework file-convention routes —
`app/manifest.ts`, `app/robots.ts`, `app/sitemap.ts`, `app/opengraph-image.tsx` — produce URLs that exist in no
directory listing.** Check the served response before claiming an asset is missing.

### 11.8 Five disk stores, no shared registry — scope note for "reset local storage"

1. IndexedDB `plantgeo-query-cache` / `layer-query-entries` — allowlisted layer reads (`lib/cache/*`). **The one
   this feature is about.**
2. IndexedDB `plantgeo-offline` v2 / `sync-queue` + `sync-conflicts` — outbound mutation replay, a **second
   independent wrapper** (`lib/offline/indexed-db.ts`, `keyPath:"id"`, no TTL/LRU/schema-version).
3. CacheStorage `plantgeo-v2` — app shell + static tile hosts, 500 MB cap (`public/sw.js`).
4. localStorage `plantgeo-layer-opacity` (`layer-store.ts:57`).
5. localStorage `plantgeo-search` (`search-store.ts:31`).

**None share a registry, naming convention, version scheme or reset path.** Per-timeline reset targets **#1
only**. Say so in the UI; do not let it read as "clear everything".

**Dead scaffolding, do not mistake for a working path:** `time-slider-store.ts:421`
`hydratePersistedLayerDates` exists and its own doc at `:359-374` states plainly that **nothing persists this
store today**, verified 2026-08-09.

---

## 12. Memory: the verdict, and what "continuous aggregate" can actually mean here

Owner, 2026-08-17: *"still seeing maxed out memory usage it does not matter what I throttle it to it always
expands to the max amount"* · *"the new logic should make these views update with newly fetched data without
recomputing materialized views using the continuous aggregate feature — disk volume size is not an issue, RAM
memory usage is the issue."* Supplied source:
`https://oneuptime.com/blog/post/2026-02-02-timescaledb-high-ingestion/view`, captured in full at
[`.omc/research/timescaledb-high-ingestion-2026-08-17.md`](../.omc/research/timescaledb-high-ingestion-2026-08-17.md).

### 12.1 Why throttling never helps — the gauge is not measuring what it looks like

**The steady 3 GB reading is page cache, counted through the cgroup. It rises to fill whatever limit is set, at
any limit.** That is the complete explanation for "it always expands to the max no matter what I throttle it
to", and it is not a defect. §5 carries six independent proofs. **Lowering the cap lowers the number the gauge
saturates at; it does not reduce pressure.** Stop reading the steady figure as a problem.

Three different things get conflated under "memory is maxed" and only one is worth engineering:

- **(a) steady gauge — page cache.** Cannot fall, is not pressure. Judge changes by idle/burst window sampling,
  never the lifetime figure.
- **(b) base reservation — real but capped and small.** `autovacuum_max_workers = 10` ×
  `maintenance_work_mem = 128 MB` ≈ 1.28 GB, plus TimescaleDB launcher/scheduler workers. **The worker cut is ON
  HOLD** (§3) because stale autoanalyze on `geo.features` was half the planner collapse.
- **(c) burst pressure — the real axis.** A pulse burst read **1,203 MB in a single 15 s interval**. Tile queries
  scan a 7,219 MB table through a **1,318,892-entry** shared GiST tree and detoast 7–10 jsonb keys per row from a
  1,467 MB TOAST relation. **§10 is the structural answer and it is unchanged by anything here.**

### 12.2 The supplied article does not apply, and its numbers are dangerous here

Every figure in it is scaled to a **64 GB dedicated server** — `shared_buffers 16GB`, `effective_cache_size
48GB`, `maintenance_work_mem 2GB`. Prod is a **3 GB-capped** Railway Postgres currently at `shared_buffers
256 MB` / `effective_cache_size 2 GB` / `work_mem 16 MB`. **Do not import these values.** The article also never
diagnoses unbounded memory growth — it has no such section; its memory content is prescriptive sizing, not
pathology. Its one directly applicable knob, `timescaledb.max_background_workers`, points the *opposite* way
here: it says raise it to 8, whereas TimescaleDB on this box is delivering nothing and should be reduced or
dropped.

### 12.2b MEASURED 2026-08-17 — the RAM fault nobody had found, and a livelock

**Everything below is measured against prod (PG 18.4 / PostGIS 3.6.4 / TimescaleDB 2.29.0), read-only.
It supersedes the parts of §12.3–12.4 it contradicts, and those corrections are marked inline.**

**THE FINDING: the refresh lane is failing on `/dev/shm`, which is RAM.** Reproducing
`mv_soil_survey_grid`'s defining query read-only:

```
ERROR after 55.05s: DiskFull: could not resize shared memory segment
"/PostgreSQL.667954444" to 16777216 bytes: No space left on device
```

**Control, same query, `max_parallel_workers_per_gather = 0`: no failure**, ran past 280 s.
`dynamic_shared_memory_type = posix`, so parallel-query DSM segments are files in **`/dev/shm` — tmpfs, i.e.
RAM, counted inside the 3 GB cgroup cap.** This is parallel-query shared-memory exhaustion. It is **not** a
statement timeout (the cap is 300 s; it died at 55 s) and **not** the GEOS bug. **Both census plans use
`Gather Merge`, so both are one allocation away from the same fault.** This is a genuine, previously
undiagnosed mechanism by which this workload consumes RAM — and it is directly actionable.

**THE SECOND FINDING: a permanent retry livelock.** `upsert_matview_refresh_state.sql:51` does
`refreshed_at = COALESCE(EXCLUDED.refreshed_at, existing)` and `matview_refresh.py:645` passes NULL on
failure; `matview_refresh.py:681-682` then returns `True, "never successfully refreshed"`. **A view that can
never succeed is therefore eligible on every tick forever, with no backoff.** Cost: **~731 s (12.2 min) of
guaranteed-doomed work per hour** against an 1,800 s budget. `agri.job_attempt` over 48 h: **46 failed, 7
deferred, 0 succeeded.** Meanwhile `agri.source_release`'s newest `retrieved_at` is **2026-08-09** — **0 new
releases in 7 days** — so `mv_signal_observation_day` burns 301 s hourly recomputing a view whose source has
not moved in 8 days. **Nothing in this runbook previously recorded this.**

**WHY THE FEATURE CENSUS COSTS 302 s — decomposed by measurement** (`vegetation`, 184,943 rows,
`EXPLAIN (ANALYZE, BUFFERS)`):

| variant | plan | spill | exec |
|---|---|---|---|
| `count(*)` only | **HashAggregate**, Memory 8,281 kB | **0** | **0.82 s** |
| + `count(DISTINCT geometry_id)` | GroupAggregate + Sort, quicksort 14,814 kB | 0 | 2.17 s |
| + `MAX(…properties…)` | GroupAggregate + Sort | **external merge Disk 118,504 kB** | 7.74 s |
| **both (the live DDL)** | GroupAggregate + Sort | **external merge Disk 118,504 kB** | **27.0 s** |

**`count(DISTINCT geometry_id)` forces sort-instead-of-hash; `MAX(properties->>'observedAt' …)` makes each
sorted tuple 511 bytes wide** by dragging the whole jsonb through the sort — ~1.4 GiB of sort input at full
scale against 32 MB `work_mem`. **The day expression is NOT the cost**; the pure day axis is 0.82 s with zero
spill. `mv_signal_observation_day` is a different shape entirely — **not a sort problem**, but ~22 GB of
sequential heap reads (two scans of an 11 GB heap) per refresh, with no usable index.

**Delta refresh measured: 14.0 s vs 302 s, spill eliminated** (36 changed groups over 24 h, LATERAL /
nested-loop shape, `Sort Method: quicksort Memory: 165 kB`). **But the naive join shape measured 98 s with a
full seq scan of 5,010,553 rows and ~52 GB of buffer traffic** — worse than useless. Shape is load-bearing.

**Change rate:** 1 h → 411 rows / **2 distinct (layer, day) groups**; 24 h → 25,049 rows / **36 groups**.
The matview holds 11,225 rows.

**RANKED FIX ORDER (measured, supersedes §12.4's ordering):**

1. **Pin `max_parallel_workers_per_gather = 0`** in `matview_refresh.py:510-516`. Stops the `/dev/shm` fault —
   **the single change that most directly reduces the RAM being measured.** Must land WITH #3.
2. **Break the livelock** — consecutive-failure backoff in `_eligibility`, needing a `consecutive_failures`
   column on `agri.matview_refresh_state`. Pure waste removal, zero correctness risk.
3. **Re-grain the feature census** — split `newest_observed_at` and the distinct/metric counts out, so the day
   axis is the 0.82 s HashAggregate path. ~22 s vs 302 s *(extrapolated)*.
4. **Delta-upsert the feature census**, LATERAL shape, **plus an explicit emptied-group DELETE**.
5. **Signal census: delta on `source_release_id`, NOT `updated_at`** — re-measure after #2 first.
6. **Weekly full reconcile** off the hourly lane, diff recorded in the ledger — the only honest equivalence proof.

**#1 + #2 + #3 are cheap and low-risk and may bring the lane inside its cap with no delta machinery at all.
§12.4 is worth building but it is FOURTH, not the whole answer.**

**PREREQUISITE BUG for any delta work:** `sql/ingest/link_feature_geometry.sql:97-100` updates
`geo.features.geometry_id` but **does not set `updated_at`**. `refresh_features.sql:131` and
`src/lib/server/services/ingest.ts:111` both do. So the watermark is maintained **by convention in 2 of 3
writers**, and `observation_count` / `unlinked_count` / `distinct_key_count` are driven entirely by
`geometry_id` — a delta keyed on `updated_at` would silently miss every linking pass.

**WHERE THIS RUNBOOK WAS WRONG — corrected by measurement:**

1. **"`id` carries FKs" (§5, §12.3) is FALSE.** `pg_constraint` returns **zero** FKs referencing
   `agri.signal_observation.id`, and **zero** referencing `geo.features.id`. It was cited as a structural
   blocker and is not one. Worse, `uq_signal_observation_release_cell_signal_time` **already contains
   `observed_at`**, so a hypertable-legal key already exists.
2. **§12.4's `WHERE updated_at > <watermark>` is inapplicable to half the problem** — **`agri.signal_observation`
   has no `updated_at` column at all.** Its available key is `source_release_id`.
3. **`mv_soil_survey_grid`'s cause was never recorded** — it is the `/dev/shm` fault above, not GEOS and not
   time. Live ledger durations are 74 s / 54 s, not the 66 s / 133 s recorded in §2.
4. **`create_hypertable(migrate_data => true)` as "a RAM event" is backwards** — per-index build memory is
   bounded by `maintenance_work_mem` (128 MB); the real costs are ~26 GB of WAL, a disk doubling and a long
   exclusive lock *(reasoning, not measured)*. Since disk is not a constraint, conversion is **more** feasible
   than §12.3 recorded — it simply buys nothing (see below).
5. **`mv_feature_observation_day` does NOT "fail every pulse"** (§9 C3) — it is **marginal**: last success
   2026-08-16 19:59:54, 301.5 s against a 300 s cap. `mv_signal_observation_day` has genuinely **never**
   succeeded (`refreshed_at` NULL).
6. **`drizzle/0029_pre_aggregation_layer.sql:72-77` raises an exception to guarantee
   `ix_features_layer_observation_day` exists**, on the grounds that without it the refresh seq-scans a 3,677 MB
   heap. **The live plan seq-scans anyway, twice, with the index present.** 284 MB serving nothing here.

**AND THE CAgg VERDICT IS NOW PROVEN, not argued:** with parallel costs forced to zero,
`count(*)` yields `Finalize GroupAggregate`/`Partial GroupAggregate` and `max(timestamptz)` carries
`aggcombinefn` — both partializable. **`count(DISTINCT geometry_id)` yields a plain `GroupAggregate` with no
Partial/Finalize at any cost setting.** Continuous aggregates store partials and finalize at read time, so
**`count(DISTINCT …)` is structurally incompatible with a CAgg** — demonstrated on this box.
`mv_signal_observation_day` additionally reads two views, uses `UNION ALL`, and uses `count(DISTINCT cell_id)`.
**Do not convert `agri.signal_observation`**: not because it is impossible (the FK blocker is fictional and disk
is free) but because **the CAgg it would enable cannot be built anyway.** If a hypertable is ever justified,
justify it with **`geo.mv_signal_cell_daily`** — 24,958,092 rows, 6,349 MB, **1,729 s measured refresh** — the
one relation where chunk-wise incremental refresh would genuinely pay.

**§10 impact:** the eight per-feature `tile_*` relations are **projections, not aggregates** — wrong shape for a
CAgg, but *safer* to delta than the census (keyed on feature id, so a vanished row deletes by id and there is no
emptied-group problem). **`tile_fire_detections_detail` (3,009,567 rows) must never be a full
`REFRESH CONCURRENTLY`** — that rebuilds 3M rows plus a GiST tree every pass. The fire grid rollups
(`cell × day`, `count(*)`, `max(frp)`) are **the only genuinely CAgg-shaped thing in the codebase** — and they
fail on *source* shape, not aggregate shape. **§10's refresh-budget caveat is understated: the lane has 0
successful attempts in 48 hours. Adding 11 specs before #1 and #2 land makes it strictly worse.**

### 12.3 The owner's principle is RIGHT; the named mechanism is BLOCKED

**The principle — refresh incrementally, touch only what changed, never recompute the whole relation — is
correct and is exactly the fix for the failing lane.** `mv_feature_observation_day` (302 s) and
`mv_signal_observation_day` (301 s) blow a 300 s cap **because `REFRESH MATERIALIZED VIEW` recomputes
everything**, every pulse, forever. That is the RAM and time cost the owner is pointing at, and C3 proves it is
also the causal spine of every staleness symptom in the UI.

**But a continuous aggregate requires a hypertable, and this database has no useful one.** §5,
"THERE ARE ZERO USEFUL HYPERTABLES": `agri.signal_observation` is a **plain heap table**; nothing ever called
`create_hypertable` (repo-wide grep returns nothing). The only hypertable, `tracking.positions`, holds **0 chunks
/ 40 kB**. No hypertable → no chunks → **no continuous aggregates and no columnar compression.**

Conversion is not a small step, and two of its blockers are structural rather than budgetary — so *"disk is not
an issue"*, while true and helpful, does not by itself unblock it:

- **`pk_signal_observation PRIMARY KEY (id)` is illegal** on a hypertable partitioned by `observed_at` — a
  hypertable's unique constraints must contain the partitioning column, and `id` carries FKs.
- **`geo.features` is worse:** its observation day is `geo.feature_observation_day(properties)`, a **function
  over jsonb**, not a column. A hypertable needs a real time column, so `geo.features` cannot be converted at all
  without first materialising `observed_day` as a stored column.
- `create_hypertable(migrate_data => true)` over 46M rows / 26 GB rewrites the whole table **on the 3 GB box this
  workstream exists to protect** — a RAM event, not a disk one.
- `compress_after` collides head-on with the data-completion workstream, which is **historical backfill**;
  compressed chunks block or badly slow inserts into them.

### 12.4 What to build instead — same property, no conversion

**Incremental delta refresh on the watermark ledger that already exists.** Replace
`REFRESH MATERIALIZED VIEW` on the two census relations with a plain table maintained by
`INSERT … SELECT … WHERE updated_at > <last watermark> … ON CONFLICT DO UPDATE`, driven by
`agri.matview_refresh_state` — the ledger the lane already writes through
(`jobs/matview_refresh.py`, `db/agri/tables/matview_refresh_state.sql`).

This is **O(rows changed), not O(table)** — the exact property a continuous aggregate provides — using machinery
that is already built, already tested and already on a schedule. No hypertable, no PK surgery, no 26 GB rewrite,
no compression/backfill conflict. It also removes the 300 s cap failure by construction rather than by raising
the cap.

**Ordering, and it matters:** the two census matviews gate every slider (C3), so fixing them is what makes a
newly-ingested day *selectable* — which is the precondition for §11's sync track ever showing a fresh day. **§11
and §12.4 are coupled: build the census fix, or the offline sync feature will faithfully cache a frozen axis.**

**Still worth doing, separately and with real evidence:** a proper feasibility pass on hypertable conversion for
`agri.signal_observation` specifically — measured, against prod, including whether TimescaleDB 2.29 FK support
covers the `id` references and what a `(id, observed_at)` composite key would break. **Do not treat 12.4 as a
refusal of the owner's instruction — it is the same property delivered by the route that is not blocked, with the
blocked route investigated rather than assumed.**

### 12.6 OWNER OBSERVATION 2026-08-17 (late) — idle is CLEAN, one query maxes it. This supersedes 12.1.

**Owner, with a Railway memory graph:** *"idle state is clean the moment i try to query anything it maxes out —
for 1 user mbs of memory should be fine."* Graph shape: flat at **~50–100 MB** while idle, then a **vertical jump
to ~1.00 GB the instant a query runs**, then flat at 1 GB.

**This is a materially different signal from §12.1 and it narrows the problem usefully.** §12.1's "the 3 GB gauge
is page cache and cannot fall" was measured on a long-lived instance with a warm 42 GB data dir, and it remains
true *for that steady reading*. But **an idle baseline of ~50 MB rising to 1 GB on a single query is not cache
fill — it is one query's working set**, and it is the clean confirmation of §12.1(c), the axis that was already
identified as the only one worth engineering.

**One user, one query, ~1 GB is exactly what the measurements predict**, so this is consistent, not mysterious:

- the wide feature census sorts **~1.4 GiB** (5M rows × 511-byte tuples, `external merge Disk 118,504 kB`
  measured on a *single* 184,943-row layer);
- the axis query plans `HashAggregate … **Planned Partitions: 32**` at 5,001,027 rows;
- `mv_soil_survey_grid` carries a **`Parallel Hash Left Join`** — a *shared, resizable* hash table in `/dev/shm`,
  i.e. RAM, which is what `could not resize shared memory segment` reports;
- a tile query walks a **1,318,892-entry** shared GiST tree and detoasts 7–10 jsonb keys per row from a
  1,467 MB TOAST relation, returning a **10.3 MB** vector tile at z5.

`work_mem = 16 MB` is not a ceiling on any of this: it is **per sort/hash node, per worker**, and
`max_parallel_workers_per_gather = 2` multiplies it, while a partitioned hash aggregate and a `Parallel Hash`
allocate beyond it in spill files and DSM respectively.

**So the fix list is unchanged and now has direct owner-visible evidence behind it:** §12.2b's ranked order
(parallel-worker pinning by `Parallel Hash` presence, the livelock backoff, the census re-grain) and **§10's
per-layer tile relations, which cut the tile-path resident working set from "whatever fraction of a 7,219 MB
table the envelopes touch" to ~250–600 MB.** §10 is the answer to this observation.

### 12.7 DROPPING TimescaleDB — AUTHORIZED, but it is NOT the cause. Do not expect it to fix this.

**Owner, 2026-08-17:** *"If needed drop timescale db entirely if its the main reason we are having these ram
memory issues."* **Authorization granted and recorded. But the premise is measured false, and whoever picks this
up must not spend the RAM budget expecting relief from it.**

**Measured against prod:** `timescaledb_information.hypertables` returns **one row** — `tracking.positions`,
1 dimension, **0 chunks**, compression off — and `timescaledb_information.continuous_aggregates` returns
**zero**. `agri.signal_observation` is `relkind = 'r'`, a plain heap, 26 GB / 46,068,872 rows. **TimescaleDB is
loaded and delivering nothing.**

Its actual cost is a **launcher plus background workers** — tens of MB, and it does not participate in a query's
working set at all. **It cannot account for a 50 MB → 1 GB jump on one query**, because none of the four
mechanisms in §12.6 involve it. Dropping it is worthwhile **hygiene** — it removes real base reservation, one
extension's worth of shared-memory allocation, and a `shared_preload_libraries` entry — and it is now
**authorized**. It is **not** the fix for §12.6.

**Ordering, and it matters:** drop it **after** the §12.2b fixes and §10, not before, so the relief attributable
to each is measurable rather than confounded. The owner previously *declined* dropping it (2026-08-16 morning);
that decision is now **reversed and superseded** by the authorization above.

**Before dropping, verify:** `tracking.positions` is the only hypertable and holds **0 chunks / 40 kB** (so
nothing is lost), and `tracking.positions` must be converted back to a plain table or dropped first — a
hypertable cannot survive `DROP EXTENSION timescaledb`. Check `shared_preload_libraries` and whether removing it
requires a **restart** (it does). **This is destructive DDL and needs its own window**, not a step inside
another workstream — same rule §3 applies to hypertable conversion.

### 12.5 Owner decisions taken 2026-08-17

- **`drizzle/0030`: LAND IT AND KEEP IT PERMANENTLY.** Owner chose "both" over §10's own recommendation to skip
  it. §10's note that it becomes a 400–600 MB carrying cost with one thin reader
  (`readFireDetectionsOnDay`'s bbox branch) **stands as recorded, and is accepted.** Disk is explicitly not a
  constraint. This settles §10 decision (a) and unblocks §7 step 2a.
- **Fire-detail time cap `N`: NO CAP — assume all 3,009,567 rows**, on the stated grounds that disk volume is not
  an issue. This is an **assumption drawn from the disk statement, not an explicit answer** to §10 decision (b);
  reversal is cheap (add a `WHERE observed_day >= current_date - N` and re-refresh). Flagged in §4.
- **§10 decision (c)** — whether `tile_interventions` gains a day column — **remains unanswered.** Default taken:
  **no day column**, matching today's behaviour exactly (`intervention_tiles` emits none); adding one changes what
  the slider does to that layer.

**Planner + lock settings measured 2026-08-20 — two of these change the plan.**

| setting | value | consequence |
|---|---|---|
| `enable_partitionwise_aggregate` | **off** (default) | **This is the lever that makes partitioning pay off for memory, and it is currently disabled.** The all-layer census matviews (§0.6) are exactly the workload blowing the cap. With it off they aggregate over one flat Append and peak allocation is unchanged by partitioning. Turn it **on** and re-measure — otherwise the restructure buys pruning for the layer-scoped reads and nothing at all for the censuses. |
| `enable_partitionwise_join` | **off** (default) | Same story for any join across partitioned relations. Enable with the above and measure both. |
| `max_locks_per_transaction` | **128** | 12 partitions x 11 indexes + parents is ~145 relations. **Re-creating every index inside one transaction exceeds the lock budget.** The swap must chunk index creation across transactions, and `LOCK TABLE geo.features` in the two ops scripts (§0.5) now takes 13 locks, not 1. |
| `maintenance_work_mem` | 128 MB | Per-index-build working memory during the copy. |
| `work_mem` | 16 MB | Per-sort/hash node, and the censuses use many. |
| `shared_buffers` | 256 MB | |
| `effective_cache_size` | 2 GB | 100% of the container, as §0 records. Still deliberately left alone. |
| `autovacuum_max_workers` | **3, `source=configuration file`** | The 2026-08-18 `ALTER SYSTEM` **persisted** — this partly answers §0.2's open question. Still unverified across a Railway-initiated restart. |
| `lock_timeout` | 0 (no timeout) | Must be set explicitly on the rename per §0.8; the default will let it queue indefinitely. |

Database is **37 GB** on PostgreSQL **18.4** — the `mv_signal_cell_daily` drop held.

