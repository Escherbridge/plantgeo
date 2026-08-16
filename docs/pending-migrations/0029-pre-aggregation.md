# 0029 — the pre-aggregation layer

Status: **written, reviewed, NOT applied, NOT journaled, NOT pinned.**

`drizzle/0029_pre_aggregation_layer.sql` sits in `drizzle/` rather than beside this file because
`0028_strategy_recommendations_real_geometry.sql` set that precedent — `drizzle/meta/_journal.json`
stops at idx 27 and neither 0028 nor 0029 has an entry, so `drizzle-kit migrate` cannot pick either
of them up and `/api/ready` cannot 503 on either. That coupling is the whole reason this file
exists; see `docs/pending-migrations/README.md`.

**Do not add a journal entry and do not re-pin `migration-contract.ts` until 0029 has actually run
against production.** `src/__tests__/security/readiness-migration-contract.test.ts` asserts the
contract matches the journal's newest entry, and `/api/ready` requires the pinned row to exist in
`drizzle.__drizzle_migrations`. Pinning an unapplied migration makes the readiness probe 503, which
fails the Railway healthcheck for `plantgeo-main` and takes down all 24 surfaces at once — not one.

---

## What lands

| file | what it does | applied by |
|---|---|---|
| `drizzle/0029_pre_aggregation_layer.sql` | nine matviews `WITH NO DATA`, their unique + secondary indexes, `geo.v_observation_day_census`, `geo.refresh_preaggregate(text)`, and two precondition assertions | `psql -f`, out of band |
| `scripts/apply-pre-aggregation.mjs --phase=a` | the two `CREATE INDEX CONCURRENTLY` builds + `ANALYZE geo.features` | by hand, **before** 0029 |
| `scripts/apply-pre-aggregation.mjs --phase=b` | the first populate of all nine, cheapest first | by hand, **after** 0029 |
| `alembic/versions/20260816_0024_matview_refresh_state.py` | `agri.matview_refresh_state`, three job-plane indexes, concurrent forecast refresh | `alembic upgrade head` |
| `docs/pending-migrations/0030-drop-unused-indexes.sql` | ~5.4 GB of never-read index | **owner only**, never automatically |

## Ordered apply sequence

0. Install `pg_stat_statements` and lower `timescaledb.max_background_workers` 16 → 2 (one
   restart). Record a baseline. This must land **first** so the before/after is measurable at query
   granularity rather than reconstructed from `pg_stat_activity` sampling.
1. Cancel or drain the 454 abandoned work items (414 `queued` + 39 `retry_wait` + 1 `deferred`, all
   created 2026-08-08 03:41:44, zero leases, 499 attempts burned) and the one
   `strategy_mv_refresh_batch` wedged in `retry_wait` since 2026-08-15 01:06:36. Record before/after
   counts. **The pulse re-runs the census every tick to decide about them**, so measuring anything
   on top of that churn loop gives an uninterpretable result.
2. `node scripts/apply-pre-aggregation.mjs --phase=a`.
3. `EXPLAIN` the census query; confirm the day bounds appear as **Index Cond**, not Filter. Record
   wall-clock. This is the checkpoint that proves phase A worked — an expression index with no
   statistics is still ignored, which is why phase A runs `ANALYZE` and why this step exists.
4. `psql -f drizzle/0029_pre_aggregation_layer.sql` (out of band, **no journal entry**), then
   `alembic upgrade head` for `20260816_0024`.
5. `node scripts/apply-pre-aggregation.mjs --phase=b`.
6. Deploy the pulse slice (refresh lane + the two import lines) and the read-path slice.
7. Follow-up commit: journal entries for **0028 and 0029**, `migration-contract.ts` re-pinned to
   0029 alone. Continue the hand-chosen `when` spacing (~100,000,000 ms increments; 0027 is
   1787900000000, so 0028 is 1788000000000 and 0029 is 1788100000000). The `sha256` is computed over
   the **exact final bytes** of `drizzle/0029_pre_aggregation_layer.sql` — any post-pin edit, even a
   comment, desyncs the pin.
8. If 0028 is applied in this window, **restart Martin**. It replaces
   `geo.strategy_recommendations_tiles`, nothing in `preDeployCommand`, `jobs-pulse` or drizzle
   restarts Martin, and a missing tile function 404s the entire composite and hides EVERY layer.
   0029 itself touches no tile function, deliberately.
9. Re-measure with `pg_stat_statements` and `pg_stat_database` deltas against the step-0 baseline.

---

## Column contracts other slices depend on

Most are already consumed by code written in parallel with this migration. Two are not yet, and
are pinned here so the read path has something to write against.

### `geo.v_observation_day_census` — the 24-surface day axis

```
surface_kind        'feature' | 'signal' | 'polygon'
surface_name        the catalogue name, verbatim
observed_day        date
observation_count   bigint
unlinked_count      bigint      -- 0 on the signal and polygon planes
distinct_key_count  bigint
newest_observed_at  timestamptz
metric_counts       jsonb       -- {metricKey: {"candidate": n, "unlinked": n}}, '{}' elsewhere
```

The 24 `surface_name` values this view can emit: the 11 `geo.layers` names
(`burn-severity`, `evacuation-zones`, `fire-detections`, `fire-perimeters`, `interventions`,
`sensors`, `soil-survey`, `vegetation`, `water-gauges`, `watersheds`, `weather-observations`), the
3 ERA5-Land soil streams (`soil-field-moisture`, `soil-field-temperature`, `soil-field-vpd`), the 9
NASA POWER climate streams (`climate-field-*` for `air-temperature`, `dew-point`, `precipitation`,
`relative-humidity`, `shortwave-radiation`, `wind-speed`, `soil-wetness-surface`,
`soil-wetness-root-zone`, `soil-wetness-profile`) and `drought-areas`.

### `geo.mv_soil_survey_grid` — the zoom-tier encoding, **not yet consumed by any reader**

`zoom_tier` is the integer `k` in `step = 0.125 * 2^k`, where `0.125` is
`SOIL_SURVEY_CELL_DEGREES`. That is exactly the ladder `soilSummaryCellDegrees` walks, so a reader
converts with `k = Math.log2(step / SOIL_SURVEY_CELL_DEGREES)` and introduces no new arithmetic.
`k` runs 0..12 (0.125° to 512°), which covers every viewport from a city block to the globe.
`cell_degrees` is carried alongside so the reader can emit the existing `cellDegrees` property
without recomputing it.

Columns: `zoom_tier, cell_degrees, cell_col, cell_row, drainage_class, map_unit_count,
hydric_count, rated_count`. `hydricFraction` stays a TypeScript division of `hydric_count` by
`rated_count` — both are bigints, so dividing in SQL would re-open the postgres-js bigint trap.

### `geo.mv_soil_survey_union` — **not yet consumed by any reader**

Columns: `zoom_tier ('regional-average' | 'coarse-average'), simplify_tolerance_degrees,
drainage_class, geom, map_unit_count, hydric_count, rated_count`. The `detail` tier draws real
delineations and never unions, so it has no row here.

**A reader must clip before projecting.** Each row holds one dissolved multipolygon covering the
whole surveyed extent, so `SELECT geom` without a bounding predicate detoasts the lot. Use
`WHERE geom && ST_MakeEnvelope(...)` and `ST_Intersection(geom, envelope)`.

---

## Corrections to the design, found while implementing

Each of these is a place the design as written would not have worked. They are recorded rather than
silently fixed so the pulse and read-path slices can act on the ones that are theirs.

1. **`geo.soil_survey_coverage` has no `updated_at` column.** The design's watermark for
   `mv_soil_survey_grid` / `_union` is `SELECT max(updated_at) FROM geo.soil_survey_coverage`; that
   column does not exist (`drizzle/0013_soil_survey_persistence.sql`). The ledger's write-time
   column is **`fetched_at`**, and it carries an index (`ix_soil_survey_coverage_fetched_at`), so
   `SELECT max(fetched_at) FROM geo.soil_survey_coverage` is both correct and O(1). **Pulse slice:
   this watermark query will raise `42703` as specified.**

2. **Two matviews depend on `now()`, so a source-only watermark will skip a refresh they needed.**
   - `geo.mv_drought_observation_day`'s live-edge branch carries the newest release forward to
     today, so its newest covered day advances by one calendar day with no ingest at all. Its
     watermark must carry `(now() AT TIME ZONE 'UTC')::date` as a third component beside
     `max(valid_date)` and `count(*)`.
   - `geo.mv_layer_hourly_activity` materialises a trailing-168-hour window, so it goes wrong by one
     bucket per hour with no writes. Its watermark must carry `date_trunc('hour', now())`.

   In both cases the `max_staleness` bound (24 h and 1 h) is a backstop that happens to cover it,
   but relying on the backstop means the gate is doing nothing for these two views — better to make
   the watermark honest.

3. **`geo.mv_feature_observation_day` is the one census refresh that still reads the `geo.features`
   heap and its TOAST.** `metric_counts` and `newest_observed_at` both require `properties`, so the
   expression index can serve the day bucketing but not the whole query. At the design's
   `min interval` of 900 s and a watermark of `max(geo.features.updated_at)` — which moves on every
   ingest — this can run every fifteen minutes over 1,467 MB of TOAST. **Recommendation: raise its
   `min interval` to 3,600 s.** The metric census is only read by `getMetricAtDate`'s unbboxed
   branch and its 6 h staleness bound is unaffected. The cost is already reduced as far as the DDL
   can take it: only the four metric-bearing layers (`water-gauges`, `weather-observations`,
   `fire-detections`, `fire-perimeters`) enter the metric CTE at all; the other seven never touch
   `properties` for it.

4. **`metric_counts` is valid only for an UNBBOXED metric request.** The relation is grained on
   `(surface, day)` and carries no geometry, so a viewport-scoped `getMetricAtDate` must keep
   counting its own candidates. The read-path slice already branches on exactly this
   (`environmental-read-model.ts` §summary), and the DDL comment says so — noted here so a later
   "simplification" does not collapse the two branches.

5. **`geo.uq_geometry_version` must not be dropped.** The design gated it on a `pg_constraint`
   lookup; that gate resolves to **no**. It is declared as a `UNIQUE` *constraint* in both
   `drizzle/0008_geometry_dimension.sql:56` and `src/lib/server/db/schema.ts:203`, so `DROP INDEX`
   refuses outright and dropping the constraint would delete the Type-2 geometry dimension's
   uniqueness guarantee. Zero scans is the expected reading for a constraint index — it is enforced
   on write, not read. Recorded in `0030-drop-unused-indexes.sql` group 3 so it is not re-opened.

6. **`geo.ix_geometry_geom`'s gate passed** as of 2026-08-15. Every reference to
   `geo.geometry.geom` in `src/`, `services/agri-data-service/src/agri_data_service/sql/` and
   `drizzle/*.sql` is a projection inside a simplify-for-output `CASE`
   (`environmental-read-model.ts:1343-1344`, `:4032-4033`); there is no `&&`, `ST_Intersects`,
   `ST_DWithin` or `<->` against that column anywhere. The drop stays owner-gated anyway, with the
   re-check command recorded beside it.

7. **`geo.mv_signal_cell_daily` covers only the 19 contracted signal names.** Anything outside
   `LANE_COVERAGE_CONTRACTS` — notably the NDVI/vegetation lane, which rides `geo.features` rather
   than the governed signal views — is **not** in this rollup and must not be read from it. A reader
   that asks this rollup for an uncontracted signal gets zero rows, which is honest but is not the
   same sentence as "upstream published nothing".

8. **Two NULL-in-the-unique-index hazards, found on review and fixed in the DDL.** Both would have
   produced a permanent, silent churn rather than an error: `REFRESH ... CONCURRENTLY` diffs old
   against new through the unique index, and NULLs never compare equal, so a single offending row
   is deleted and re-inserted on *every* refresh for the life of the relation.
   - `agri.signal_observation.normalized_unit` is **nullable**, and it is part of
     `uq_mv_signal_cell_daily`. The two governed views never see such a row because they JOIN on
     the unit; `geo.mv_signal_cell_daily` reads the base table, so it carries its own
     `normalized_unit IS NOT NULL` gate. This would have landed on the most expensive refresh in
     the design.
   - `ST_Centroid` of an empty or degenerate geometry returns `POINT EMPTY`, whose `ST_X` is NULL,
     so `floor(ST_X(...) / step)` is NULL — and `cell_col` / `cell_row` are the unique key of
     `geo.mv_soil_survey_grid`. The emptiness is now filtered before the lattice arithmetic, in a
     `located` CTE, and the same guard is applied to `mv_soil_survey_union`'s tiling.

9. **`db/manifest.sql` needs regenerating.** It is generated by `db/tools/split_schema.py` and has
   no entry for `agri/tables/matview_refresh_state.sql`, so a rebuild-from-scratch would omit the
   table and the schema-parity test would report drift. Run `db/tools/regenerate.py` after
   `20260816_0024` is applied; that also rewrites the four declarative files this slice hand-wrote
   to match what `pg_dump` actually emits.

---

## TimescaleDB: continuous aggregates, compression and retention

Assessed and **deferred**, with the preconditions recorded so a later track does not re-derive them.

**Continuous aggregates are unavailable, not declined.** Measured 2026-08-15,
`timescaledb_information.hypertables` holds exactly one row — `tracking.positions`, 0 chunks,
40 kB, compression disabled, 0 rows. `agri.signal_observation` (46,068,872 rows / 26 GB) and
`agri.forecast_observation` are plain heaps; `_timescaledb_catalog.continuous_agg` and
`.compression_settings` are both empty. A continuous aggregate can only be built on a hypertable,
so no relation in this design is eligible and `add_continuous_aggregate_policy` appears nowhere.
Every refresh is therefore a full `REFRESH MATERIALIZED VIEW CONCURRENTLY`, and the watermark gate
in `agri.matview_refresh_state` is what stops that from being a scheduled full rebuild forever.

**Do not convert `agri.signal_observation` to a hypertable as part of this work.**
`create_hypertable(migrate_data => true)` rewrites 26 GB in one transaction against
`maintenance_work_mem = 128 MB` on a 3 GB cgroup — strictly heavier than the refresh it would
replace — and `pk_signal_observation PRIMARY KEY (id)` is illegal on a hypertable partitioned by
`observed_at` while `id` carries inbound foreign keys.

**Compression, if a later track converts the table.** The settings are already determined by the
measured serving predicates, and a wrong `segmentby` makes compressed reads *slower*, not faster:

- `segmentby => 'cell_id, signal_name'` — all four serving predicates lead
  `cell_id … AND signal_name = $ AND support_key = $ AND normalized_unit = $`.
- `orderby => 'observed_at DESC'` — every one of them orders on `observed_at`.
- `compress_after` **must exceed the widest gap-reopen horizon**: `MAX_GAP_REOPEN_GENERATIONS = 5`
  (commit `c14e36b`) × the widest archive window. Compressing inside that horizon makes the
  `ON CONFLICT` upserts against `uq_signal_observation_release_cell_signal_time` fail or force
  decompression, and it will read as an upstream outage rather than as a storage decision.

**The accessible win on that table today is its indexes, not columnar compression.**
`agri.signal_observation` carries 15 GB of index against an 11 GB heap.
`uq_signal_observation_release_cell_signal_time` alone is 11 GB with 235,416,881 scans — it is the
ingest conflict target and cannot be dropped.

**Retention is not recommended yet.** `SIGNAL_CELL_DAILY_RETENTION_DAYS` is named in the design as a
sizing knob and deliberately left un-set: the plane is only ~1,470 days deep (NASA POWER's floor is
2022-08-06), correctness comes first, and trimming before `pg_stat_statements` can give a
before/after would delete history to solve a problem nobody has measured.

**Estimated size effect of what this design actually delivers** (no compression, no conversion):

| lever | reclaimed |
|---|---|
| drop 3 ungated never-read indexes (`0030`) | **5,209 MB** |
| drop `ix_geometry_geom` (gated, gate passed) | 183 MB |
| drop `idx_features_layer_updated_at` (gated, EXPLAIN required) | 72 MB |
| `ix_features_layer_observation_day` | −250 MB (cost) |
| `ix_features_updated_at` | −110 MB (cost) |
| nine new rollups | −~3.3 GB (cost, dominated by `mv_signal_cell_daily`) |

Net on-disk change is roughly **+2 GB**, and that is the wrong number to look at. The number that
matters is the **per-request working set**, which goes from "sequential-scan a 3,677 MB heap plus
1,467 MB of TOAST, thirteen times" to "an index range read of 10²–10³ kB". `mv_signal_cell_daily` is
3.2 GB on disk against a 26 GB source, and because a matview is written in its defining query's
physical order and a full `REFRESH` rewrites it, a serving read is a **contiguous ~240 kB range**
rather than a scatter across 11 GB.

---

## Things this migration deliberately does not do

- **No `CONCURRENTLY` DDL in any drizzle file.** The migrator wraps each file in a transaction;
  `--> statement-breakpoint` splits statements but they share it, and PostgreSQL raises `25001`
  *before* checking `IF NOT EXISTS`. 0029 asserts the two concurrent indexes exist and raises a
  named exception if they do not, so a deploy that skipped the ops script fails loudly instead of
  silently regressing to sequential scans.
- **No index drops.** All six are `DROP INDEX CONCURRENTLY`, all are irreversible on a 3 GB box, and
  they live owner-gated in `0030-drop-unused-indexes.sql`.
- **No tile function is touched**, so no Martin restart is required *by 0029*.
- **No data is synthesised.** Every rollup reads only what is stored. If a source is empty the
  rollup is empty, and that is the correct answer — `interventions` will have a
  `mv_layer_feature_stats` row reading zero rather than no row at all, because a layer that exists
  and is empty and a layer that does not exist are different sentences.
- **`geo.osm_roads` / `geo.osm_waterways`** (0 rows, but live in `infra/martin/martin.yaml:50-58`,
  `sources.ts:51`, `layers.ts:459,471`) are **out of scope**. They must be removed as a four-file
  atomic change or not at all — a missing Martin source 404s the whole composite.
