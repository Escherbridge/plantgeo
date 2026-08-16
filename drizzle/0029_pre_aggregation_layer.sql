-- The pre-aggregation layer: nine matviews, one union view, and one refresh door, so that no
-- application request makes the database read more rows than it returns by more than a bounded,
-- documented factor.
--
-- WHY, in one paragraph. The production timeseries database is capped at 3 GB of RAM by a Railway
-- cgroup with zero active users. `agri.signal_observation` alone is 46,068,872 rows / 26 GB, and
-- `geo.features` is 4.97M rows carrying 1,467 MB of TOAST behind `properties`. The serving paths
-- that grouped those tables per request -- the slider capability axis, the metric census, the
-- drought release resolver, the SSURGO union -- were reading tens of millions of rows to return
-- tens of thousands, and on 2026-08-15 the stream axis spent the whole Cloudflare origin budget
-- and returned 524, taking every slider down at once. The fix is not caching and not more RAM: it
-- is to precompute each of those answers into a small, clustered relation and read it by index.
-- Latency is NOT the constraint here (the owner accepts up to 2 s per slider debounce pause);
-- RESIDENT WORKING SET is. Every choice below optimises for a small working set.
--
-- WHY PLAIN MATERIALIZED VIEWS AND NOT TIMESCALEDB CONTINUOUS AGGREGATES. Measured 2026-08-15:
-- `timescaledb_information.hypertables` holds exactly one row -- `tracking.positions`, 0 chunks,
-- 40 kB, compression disabled. `agri.signal_observation` and `agri.forecast_observation` are plain
-- heaps, and `_timescaledb_catalog.continuous_agg` and `.compression_settings` are both empty. A
-- continuous aggregate can only be built on a hypertable, so it is unavailable for every relation
-- in this file, and converting `agri.signal_observation` with `create_hypertable(migrate_data)`
-- would rewrite 26 GB in one transaction against `maintenance_work_mem = 128 MB` -- strictly
-- heavier than the refresh it would replace -- and is illegal anyway while
-- `pk_signal_observation PRIMARY KEY (id)` carries inbound foreign keys.
--
-- WHY `WITH NO DATA` EVERYWHERE. Creating a matview with data would run every defining query
-- inside the drizzle migration's single transaction, during `preDeployCommand`, on the box this
-- work exists to protect. `WITH NO DATA` is instantaneous, takes no lock beyond the catalog and
-- allocates nothing. The first populate is a separate, session-tuned pass in
-- `scripts/apply-pre-aggregation.mjs` (phase B), which is safe precisely because no reader is
-- deployed yet. Every refresh after that is `REFRESH ... CONCURRENTLY`, driven by the
-- `matview-refresh` lane inside the jobs pulse, which self-heals any view it finds unpopulated.
--
-- WHY EVERY MATVIEW HAS A UNIQUE INDEX. `REFRESH MATERIALIZED VIEW CONCURRENTLY` refuses to run
-- without one, and a non-concurrent refresh takes an ACCESS EXCLUSIVE lock that blocks every read
-- of the view for its whole duration. A matview without a unique index is therefore a matview
-- that will one day blank the layer it feeds. There are no exceptions in this file.
--
-- WHAT THIS FILE DOES NOT DO, deliberately:
--   * No `CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY`. `node scripts/migrate.mjs` uses
--     drizzle-orm/postgres-js's migrator, which wraps each migration FILE in one transaction;
--     `--> statement-breakpoint` splits statements but they still share it. CONCURRENTLY raises
--     25001 there, and `IF NOT EXISTS` does not save you because PostgreSQL raises 25001 BEFORE it
--     checks existence. The two concurrent indexes are built by `scripts/apply-pre-aggregation.mjs`
--     phase A; this file only ASSERTS they exist, so a deploy that skipped the ops script fails
--     loudly instead of silently regressing to sequential scans.
--   * No `CREATE OR REPLACE` of any `*_tiles` function. Nothing in this design changes a tile
--     function, and changing one forces a Martin restart that nothing in `preDeployCommand`,
--     `jobs-pulse` or drizzle performs -- a missing tile function 404s the whole composite and
--     hides EVERY layer, not one.
--   * No index drops. The design's six drops are all `DROP INDEX CONCURRENTLY` and are parked,
--     unapplied and owner-gated, in `docs/pending-migrations/0030-drop-unused-indexes.sql`.
--
-- Rationale that does not fit a migration header lives in `docs/pending-migrations/0029-pre-aggregation.md`.

-- ---------------------------------------------------------------------------------------------
-- 0. PRECONDITION ASSERTIONS
-- ---------------------------------------------------------------------------------------------
-- `geo.mv_feature_observation_day`'s defining query buckets 4.97M `geo.features` rows by
-- `geo.feature_observation_day(properties)`. Without the expression index that is a sequential
-- scan of a 3,677 MB heap plus 1,467 MB of TOAST -- the exact read this whole file exists to
-- delete. The index cannot be created here (see the CONCURRENTLY note above), so its absence is
-- turned into a loud, actionable failure rather than a silent performance regression.
--
-- The stale comment at environmental-read-model.ts:3642-3647 claiming this index "cannot be
-- created (42P17)" is true of the INLINE day expression and false of the function: 0015 declares
-- `geo.feature_observation_day` IMMUTABLE PARALLEL SAFE, and that declaration is what makes an
-- expression index on it legal.
DO $$ BEGIN
  IF to_regclass('geo.ix_features_layer_observation_day') IS NULL THEN
    RAISE EXCEPTION
      'ix_features_layer_observation_day is missing: run `node scripts/apply-pre-aggregation.mjs --phase=a` before applying 0029';
  END IF;
END $$;
--> statement-breakpoint

-- The `matview-refresh` lane's watermark for every `geo.features`-backed view is
-- `SELECT max(updated_at) FROM geo.features`. That is only O(1) with an index leading on
-- `updated_at`; `idx_features_layer_updated_at` leads with `layer_id` and cannot serve it, which
-- is why it has read 18,949,770,956 tuples to return 31,035.
DO $$ BEGIN
  IF to_regclass('geo.ix_features_updated_at') IS NULL THEN
    RAISE EXCEPTION
      'ix_features_updated_at is missing: run `node scripts/apply-pre-aggregation.mjs --phase=a` before applying 0029';
  END IF;
END $$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 1. THE OBSERVATION-DAY CENSUS -- three matviews, one column contract
-- ---------------------------------------------------------------------------------------------
-- Every observation-bearing surface reports through ONE column contract, whatever plane backs it:
--
--   surface_kind        'feature' | 'signal' | 'polygon'
--   surface_name        the slider capability catalogue name, verbatim
--   observed_day        date
--   observation_count   rows that day
--   unlinked_count      rows with no geometry link; 0 where the concept does not apply
--   distinct_key_count  distinct geometry_id / cell_id / class that day
--   newest_observed_at  the freshest instant that day
--   metric_counts       {metricKey: {"candidate": n, "unlinked": n}}; '{}' where N/A
--
-- Three relations rather than one because their SOURCES and their REFRESH CADENCES differ -- the
-- feature plane moves whenever an ingest lands, the signal plane moves once per archive release,
-- the drought plane once a week. Folding them into one matview would make every refresh pay for
-- the slowest source. They are unioned by the plain view `geo.v_observation_day_census`, which is
-- free: it sits over roughly 35,000 rows in total, which is a sort, not a scan.
--
-- ALL THREE LIVE IN THE `geo` SCHEMA AND ARE CREATED BY THE DRIZZLE TREE, including the
-- signal-backed one, which reads `agri.` relations. Precedent: `drizzle/0016_soil_field.sql`
-- already defines `geo.soil_field_observation` as a view over `agri.signal_observation`, and
-- 0027/0028 already build `geo.` matviews over `agri.` tables. Splitting them across the two
-- migration trees would create an ordering hazard with no upside: a view cannot reference a
-- relation the other tree has not created yet, and the two trees have no shared ordering.

-- 1a. THE FEATURE PLANE -- eleven `geo.layers` rows.
--
-- `observation_count` counts ONLY rows with a geometry link, because that is exactly what
-- `readObservationWindows` counted before this matview replaced it (`f.geometry_id IS NOT NULL`),
-- and an axis that counted orphans would offer days no reader can draw. The orphans are not
-- discarded -- they are reported separately as `unlinked_count`, which is the number
-- `getMetricAtDate` needs to say "N of M observations on this date are not yet linked to a place"
-- instead of falsely reporting a partially-orphaned day as complete.
--
-- Rows whose publisher-named day does not PARSE are excluded rather than grouped under a NULL
-- day. `REFRESH ... CONCURRENTLY` diffs old against new through the unique index, and NULLs never
-- compare equal, so a NULL-day row would be deleted and re-inserted on every single refresh
-- forever. The prior query grouped them under NULL; that was invisible only because the window
-- pipeline discarded them downstream.
--
-- `newest_observed_at` is guarded by `pg_input_is_valid` before the cast for the same reason
-- `geo.feature_observation_day` guards `to_date`: `properties->>'observedAt'` can pass the
-- ten-character day regex and still be an invalid timestamptz, and one raise inside a matview's
-- defining query fails the ENTIRE refresh, not one row. Same COALESCE order as
-- PUBLISHER_NAMED_DAY_RULE (`observedAt`, `updatedAt`, `polygonDateTime`) -- if the two orders
-- ever fork, this column dates a row differently from the day it is filed under.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_feature_observation_day AS
WITH day_total AS (
  SELECT
    l.name AS surface_name,
    geo.feature_observation_day(f.properties) AS observed_day,
    COUNT(*) FILTER (WHERE f.geometry_id IS NOT NULL)::bigint AS observation_count,
    COUNT(*) FILTER (WHERE f.geometry_id IS NULL)::bigint AS unlinked_count,
    COUNT(DISTINCT f.geometry_id)::bigint AS distinct_key_count,
    MAX(
      CASE
        WHEN pg_input_is_valid(
               COALESCE(
                 f.properties ->> 'observedAt',
                 f.properties ->> 'updatedAt',
                 f.properties ->> 'polygonDateTime'
               ),
               'timestamptz'
             )
          THEN COALESCE(
                 f.properties ->> 'observedAt',
                 f.properties ->> 'updatedAt',
                 f.properties ->> 'polygonDateTime'
               )::timestamptz
      END
    ) AS newest_observed_at
  FROM geo.layers AS l
  JOIN geo.features AS f ON f.layer_id = l.id
  WHERE f.status = 'published'
    AND geo.feature_observation_day(f.properties) IS NOT NULL
    -- PUBLISHER_NAMED_DAY_RULE PARITY, and it is not optional. `geo.feature_observation_day`
    -- COALESCEs FOUR keys; the axis this matview replaces (`readObservationWindows`) and the
    -- candidate/page CTEs of `getMetricAtDate` both use OBSERVATION_DAY, built from the THREE
    -- shared keys, and both require `OBSERVATION_TIME_TEXT IS NOT NULL`. 0018 added
    -- `fireDiscoveryDateTime` as a LAST, TILE-ONLY fallback and recorded in its own header that
    -- the slider axis deliberately still buckets from the three shared keys;
    -- `observation-day-contract.test.ts` pins that split as TILE_ONLY_FALLBACK_KEYS. Without this
    -- guard the 13 published fire-perimeters rows whose only timestamp is `fireDiscoveryDateTime`
    -- would be counted here and excluded by the page CTE -- `getMetricAtDate` would report N
    -- observations on a day it renders none of, with `unlinkedCount` 0 to say nothing is wrong.
    --
    -- SCOPED TO ONE LAYER on purpose. 0018:34 records that no other layer in the warehouse carries
    -- a `fireDiscoveryDateTime` key at all, so the guard is a no-op everywhere else -- and
    -- PostgreSQL short-circuits OR left to right, so the other ten layers never evaluate the
    -- COALESCE, never detoast `properties`, and keep the expression-index path this file's
    -- precondition assertion exists to protect.
    AND (
      l.name <> 'fire-perimeters'
      OR COALESCE(
           f.properties ->> 'observedAt',
           f.properties ->> 'updatedAt',
           f.properties ->> 'polygonDateTime'
         ) IS NOT NULL
    )
  GROUP BY l.name, geo.feature_observation_day(f.properties)
),
-- The metric census, restricted to the FOUR layers that actually back a slider metric. The other
-- seven never enter this CTE at all, so their days are answered from the expression index without
-- ever touching `properties` -- which is the whole point, because touching `properties` is what
-- detoasts 1,467 MB.
--
-- Built per METRIC KEY, never by `jsonb_each` over 4.97M rows. The list below is transcribed from
-- `METRIC_SOURCES` in src/lib/server/services/environmental-read-model.ts and must move with it;
-- `src/__tests__/services/signal-census-contract.test.ts` is what holds the two together.
--
--   missing_sentinel  the exact upstream "no reading" marker to exclude. -999999 is
--                     USGS_NO_DATA_SENTINEL; 0 is the FIRMS parser placeholder (measured on
--                     production 2026-08-05: all 6,297 stored detections carry brightness 0 and 6
--                     carry frp 0 -- a brightness temperature of 0 K is physically impossible and
--                     a detection radiating exactly 0 MW is not an observation either).
--   viirs_only        fire-radiative-power only. FRP is integrated over the sensor pixel and is
--                     not comparable across instruments: measured on production 2026-08-05,
--                     MODIS_SP median FRP 33.10 MW against VIIRS 4.27 -- an ~8x gap that is pixel
--                     area, not fire intensity. Rows written before 2026-08-05 carry no `product`
--                     key at all and are VIIRS by construction, so absence must pass.
metric_day AS (
  SELECT
    l.name AS surface_name,
    geo.feature_observation_day(f.properties) AS observed_day,
    metric.metric_key,
    COUNT(*)::bigint AS candidate_count,
    COUNT(*) FILTER (WHERE f.geometry_id IS NULL)::bigint AS unlinked_count
  FROM geo.layers AS l
  JOIN geo.features AS f ON f.layer_id = l.id
  JOIN (
    VALUES
      ('water-gauges'::text,        'streamflow-cfs'::text,      'flowCfs'::text,          -999999::double precision, false),
      ('weather-observations',      'temperature',               'temperature',            NULL::double precision,    false),
      ('weather-observations',      'humidity',                  'humidity',               NULL::double precision,    false),
      ('weather-observations',      'precipitation',             'precipitation',          NULL::double precision,    false),
      ('weather-observations',      'wind-speed',                'windSpeed',              NULL::double precision,    false),
      ('weather-observations',      'wind-direction',            'windDirection',          NULL::double precision,    false),
      ('fire-detections',           'fire-radiative-power',      'frp',                    0::double precision,       true),
      ('fire-detections',           'fire-brightness',           'brightness',             0::double precision,       false),
      ('fire-perimeters',           'perimeter-acres',           'gisAcres',               NULL::double precision,    false),
      ('fire-perimeters',           'percent-contained',         'percentContained',       NULL::double precision,    false)
  ) AS metric(surface_name, metric_key, value_key, missing_sentinel, viirs_only)
    ON metric.surface_name = l.name
  WHERE f.status = 'published'
    AND geo.feature_observation_day(f.properties) IS NOT NULL
    -- The same three-key parity guard as `day_total`, for the same reason and with more teeth:
    -- `getMetricAtDate` reads `metric_counts->'perimeter-acres'->>'candidate'` off this relation
    -- and its page CTE filters on OBSERVATION_DAY. If the two populations fork, the unlinked
    -- notice prints a denominator no drawable row can account for.
    AND (
      l.name <> 'fire-perimeters'
      OR COALESCE(
           f.properties ->> 'observedAt',
           f.properties ->> 'updatedAt',
           f.properties ->> 'polygonDateTime'
         ) IS NOT NULL
    )
    AND jsonb_typeof(f.properties -> metric.value_key) = 'number'
    -- The `::double precision` cast is wrapped in a CASE rather than left to sit beside the
    -- jsonb_typeof test as a sibling AND. `AND` does not guarantee evaluation order, and one
    -- failed cast here does not fail one row or one request -- it fails the ENTIRE refresh of
    -- this matview. CASE is the only construct PostgreSQL guarantees will not evaluate its
    -- untaken branch, which is the same reason `geo.feature_observation_day` guards `to_date`.
    AND (
      metric.missing_sentinel IS NULL
      OR CASE
           WHEN jsonb_typeof(f.properties -> metric.value_key) = 'number'
             THEN (f.properties ->> metric.value_key)::double precision <> metric.missing_sentinel
           ELSE false
         END
    )
    AND (
      NOT metric.viirs_only
      OR COALESCE(f.properties ->> 'product', '') NOT LIKE 'MODIS%'
    )
  GROUP BY l.name, geo.feature_observation_day(f.properties), metric.metric_key
),
metric_counts AS (
  SELECT
    surface_name,
    observed_day,
    jsonb_object_agg(
      metric_key,
      jsonb_build_object('candidate', candidate_count, 'unlinked', unlinked_count)
    ) AS metric_counts
  FROM metric_day
  GROUP BY surface_name, observed_day
)
SELECT
  'feature'::text AS surface_kind,
  day_total.surface_name,
  day_total.observed_day,
  day_total.observation_count,
  day_total.unlinked_count,
  day_total.distinct_key_count,
  day_total.newest_observed_at,
  COALESCE(metric_counts.metric_counts, '{}'::jsonb) AS metric_counts
FROM day_total
LEFT JOIN metric_counts
  ON metric_counts.surface_name = day_total.surface_name
 AND metric_counts.observed_day = day_total.observed_day
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_feature_observation_day
  ON geo.mv_feature_observation_day (surface_name, observed_day);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_feature_observation_day IS
  'One row per (geo.layers.name, publisher-named day) over published geo.features: the day axis, '
  'the orphan count, and per-metric candidate/unlinked totals. Retires readObservationWindows and '
  'the unbounded half of getMetricAtDate. metric_counts is valid ONLY for an unbboxed request -- '
  'this relation is grained on (surface, day) and carries no geometry, so a viewport-scoped '
  'metric read must still count its own candidates.';
--> statement-breakpoint

-- 1b. THE SIGNAL PLANE -- twelve streams that have no `geo.layers` row.
--
-- Reads the two GOVERNED VIEWS, never `agri.signal_observation` directly. Their VALUES joins
-- already apply the unit, lane and quality gates the drawing readers apply (`is_observed`,
-- `quality_flag = 'accepted'`, a non-null normalized value, and for the NASA POWER lane the
-- `source.key = 'nasa-power-daily'` lane gate), so a day this axis offers is a day those readers
-- can actually answer. Grouping the base table instead would publish days the field readers
-- reject, which is the precise class of false claim this platform keeps having to correct.
--
-- The measure -> stream and signal -> stream maps below are transcribed from
-- `readStreamObservationWindows`. They are spelled out here rather than derived because a matview
-- has no access to the TypeScript constants; `src/__tests__/services/pre-aggregation-catalogue.test.ts`
-- asserts this DDL's `surface_name` set against a HAND-SPELLED 24-name list, which is what keeps
-- the two halves from drifting together and still passing.
--
-- `unlinked_count` is 0 and `metric_counts` is '{}' on this plane by construction: a signal
-- observation is keyed to a lattice cell that always exists, so it cannot be orphaned, and no
-- slider metric is backed by these streams.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_signal_observation_day AS
SELECT
  'signal'::text AS surface_kind,
  stream.layer_name AS surface_name,
  soil.observed_day,
  COUNT(*)::bigint AS observation_count,
  0::bigint AS unlinked_count,
  COUNT(DISTINCT soil.cell_id)::bigint AS distinct_key_count,
  MAX(soil.observed_at) AS newest_observed_at,
  '{}'::jsonb AS metric_counts
FROM geo.soil_field_observation AS soil
JOIN (
  VALUES
    ('moisture'::text,    'soil-field-moisture'::text),
    ('temperature',       'soil-field-temperature'),
    ('vpd',               'soil-field-vpd')
) AS stream(measure, layer_name) ON stream.measure = soil.measure
-- SOIL_FIELD_SUPPORT_KEY. The ERA5-Land lane's support key, applied here exactly as
-- getPublishedSoilField applies it.
WHERE soil.support_key = 'era5-land-0.1deg'
GROUP BY stream.layer_name, soil.observed_day

UNION ALL

SELECT
  'signal'::text AS surface_kind,
  stream.layer_name AS surface_name,
  climate.observed_day,
  COUNT(*)::bigint AS observation_count,
  0::bigint AS unlinked_count,
  COUNT(DISTINCT climate.cell_id)::bigint AS distinct_key_count,
  MAX(climate.observed_at) AS newest_observed_at,
  '{}'::jsonb AS metric_counts
FROM geo.climate_field_observation AS climate
JOIN (
  VALUES
    ('air-temperature'::text,      'climate-field-air-temperature'::text),
    ('dew-point',                  'climate-field-dew-point'),
    ('precipitation',              'climate-field-precipitation'),
    ('relative-humidity',          'climate-field-relative-humidity'),
    ('shortwave-radiation',        'climate-field-shortwave-radiation'),
    ('wind-speed',                 'climate-field-wind-speed'),
    ('soil-wetness-surface',       'climate-field-soil-wetness-surface'),
    ('soil-wetness-root-zone',     'climate-field-soil-wetness-root-zone'),
    ('soil-wetness-profile',       'climate-field-soil-wetness-profile')
) AS stream(signal, layer_name) ON stream.signal = climate.signal
-- CLIMATE_FIELD_SUPPORT_KEY. Not redundant with the view's own lane gate: `surface` is a generic
-- support the ERA5-Land writer also emits.
WHERE climate.support_key = 'surface'
GROUP BY stream.layer_name, climate.observed_day
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_signal_observation_day
  ON geo.mv_signal_observation_day (surface_name, observed_day);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_signal_observation_day IS
  'One row per (stream name, day) for the twelve signal-backed slider streams -- three ERA5-Land '
  'soil measures and nine NASA POWER climate signals -- grouped from the two governed views. This '
  'is the relation that retires the ~17M-row whole-table pass that returned a Cloudflare 524 on '
  '2026-08-15 and took every slider down at once.';
--> statement-breakpoint

-- 1c. THE POLYGON PLANE -- drought, weekly releases expanded to the days they cover.
--
-- USDM publishes one release per week, valid on a Tuesday, and a release COVERS the days until the
-- next one supersedes it. Feeding the valid dates raw would report six days in seven as
-- unpublished -- the exact false observed-absence this layer keeps producing. The expansion below
-- is byte-for-byte the same bounded carry-forward `resolveDroughtRelease` applies, so a week the
-- record genuinely skips is a gap on the axis AND a gap in the reader, and neither invents the
-- other:
--
--   * with a later release stored, coverage stops the day before it, capped at the release's own
--     seven-day week (DROUGHT_RELEASE_INTERVAL_DAYS), so a skipped week stays EMPTY;
--   * at the live edge there is no next release, so the newest one carries forward at most 14 days
--     (DROUGHT_MAX_CARRY_FORWARD_DAYS = DROUGHT_MAX_AGE_MS / 86_400_000) -- USDM publishes the
--     Tuesday-valid map on the following Thursday, so the newest release is routinely 7-9 days old
--     and refusing it would blank the layer two days a week;
--   * and never past today, because a cached row must not claim a future day as observed.
--
-- NEVER SELECTS `geom`. `geo.drought_areas` is 640 kB of heap hiding 495 MB of TOAST across 1,040
-- rows; any unbounded projection pulls the lot. Only `valid_date`, a class count and `ingested_at`
-- cross this boundary.
--
-- REFRESH-CADENCE HAZARD, stated rather than hidden: the live-edge branch depends on `now()`, so
-- this view's newest covered day advances by one calendar day even when NOTHING is ingested. A
-- watermark of `(max(valid_date), count(*))` alone will therefore skip a refresh this view needed.
-- The lane's watermark for this view MUST carry the current UTC date as a third component. Written
-- here because this is the relation whose correctness depends on it.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_drought_observation_day AS
SELECT
  'polygon'::text AS surface_kind,
  'drought-areas'::text AS surface_name,
  covered.covered_day::date AS observed_day,
  release.class_count AS observation_count,
  0::bigint AS unlinked_count,
  release.class_count AS distinct_key_count,
  release.newest_ingested_at AS newest_observed_at,
  '{}'::jsonb AS metric_counts
FROM (
  SELECT
    d.valid_date::date AS valid_date,
    LEAD(d.valid_date::date) OVER (ORDER BY d.valid_date::date) AS next_valid_date,
    COUNT(*)::bigint AS class_count,
    MAX(d.ingested_at) AS newest_ingested_at
  FROM geo.drought_areas AS d
  GROUP BY d.valid_date
) AS release
CROSS JOIN LATERAL generate_series(
  release.valid_date::timestamp,
  LEAST(
    CASE
      WHEN release.next_valid_date IS NULL THEN release.valid_date + 14
      ELSE release.valid_date + (7 - 1)
    END,
    CASE
      WHEN release.next_valid_date IS NULL THEN (now() AT TIME ZONE 'UTC')::date
      ELSE release.next_valid_date - 1
    END
  )::timestamp,
  interval '1 day'
) AS covered(covered_day)
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_drought_observation_day
  ON geo.mv_drought_observation_day (surface_name, observed_day);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_drought_observation_day IS
  'One row per day a stored USDM release COVERS, under the same bounded carry-forward '
  'resolveDroughtRelease applies. Projects valid_date and counts only -- never geom, which hides '
  '495 MB of TOAST behind 1,040 rows. Its live-edge branch reads now(), so its refresh watermark '
  'must include the current UTC date or it will be skipped on a day it needed to advance.';
--> statement-breakpoint

-- 1d. THE UNION. A PLAIN VIEW, not a matview, and that is the cheap choice rather than a
-- compromise: it sits over roughly 35,000 rows in total across the three matviews above, each of
-- which is already indexed on `(surface_name, observed_day)`. Materialising it would add a fourth
-- copy of the same rows and a fourth refresh to schedule, to save a sort of 35,000 rows.
CREATE OR REPLACE VIEW geo.v_observation_day_census AS
SELECT surface_kind, surface_name, observed_day, observation_count,
       unlinked_count, distinct_key_count, newest_observed_at, metric_counts
FROM geo.mv_feature_observation_day
UNION ALL
SELECT surface_kind, surface_name, observed_day, observation_count,
       unlinked_count, distinct_key_count, newest_observed_at, metric_counts
FROM geo.mv_signal_observation_day
UNION ALL
SELECT surface_kind, surface_name, observed_day, observation_count,
       unlinked_count, distinct_key_count, newest_observed_at, metric_counts
FROM geo.mv_drought_observation_day;
--> statement-breakpoint

COMMENT ON VIEW geo.v_observation_day_census IS
  'The 24-surface slider day axis in one relation: 11 geo.layers names, 12 signal streams and '
  'drought-areas. The OUTER relation of the section-9 LEFT JOIN stays in TypeScript; this is the '
  'inner one. A surface_name this view cannot emit is silently dropped from the capability '
  'payload -- tiles render, history reports zero, no slider mounts.';
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 2. `geo.mv_signal_cell_daily` -- the one large rollup
-- ---------------------------------------------------------------------------------------------
-- Grain: (support_key, signal_name, normalized_unit, cell_id, observed_day) over the ~17M-row
-- governed subset of `agri.signal_observation`'s 46,068,872 rows.
--
-- NO GEOMETRY COLUMN, and this is load-bearing for the sizing. Carrying `cell_geometry` and
-- `cell_centroid` would put a polygon and a point on every one of ~17M rows. `agri.spatial_cell`
-- is ~2,000 rows and is trivially resident; every reader joins it at read time instead.
--
-- WHY THIS SHRINKS THE WORKING SET even though it is not a small table -- two independent
-- mechanisms, and the second is the larger:
--
--   1. NARROWING. A source row carries three uuids, three varchar(150)s and a `metadata_json`
--      jsonb, and the table pays for three indexes totalling 15 GB (more than its 11 GB heap;
--      `uq_signal_observation_release_cell_signal_time` alone is 11 GB with 235,416,881 scans, and
--      it is the ingest conflict target so it cannot be dropped). A rollup row carries one uuid,
--      three short texts, a date and six float8s. ~17M x ~120 B is ~2.1 GB of heap plus ~1.1 GB of
--      index -- about 3.2 GB against 26 GB.
--
--   2. CLUSTERING. A matview is written in the physical order of its defining query, and a full
--      REFRESH rewrites it, so that ordering never degrades. The trailing ORDER BY is therefore
--      not decoration: every serving read is (one support_key, one signal, one unit, one day, at
--      most ~2,000 cells), which lands as a CONTIGUOUS RANGE of roughly 240 kB. The same read
--      against raw `signal_observation`, whose physical order is ingest order interleaved across
--      signals and sources, scatters across the full 11 GB heap. This is why 3.2 GB of rollup
--      beats 26 GB of source by far more than the 8x the byte count suggests.
--
-- The GROUP BY is written in the SAME column order as the ORDER BY so the planner has a path to
-- satisfy both with one sort rather than two.
--
-- THE 19 GOVERNED SIGNAL NAMES are the lanes under contract in
-- `services/agri-data-service/src/agri_data_service/execution/coverage_contract.py`
-- (LANE_COVERAGE_CONTRACTS), verified against `agri.data_source` on 2026-08-11. They are spelled
-- out rather than joined from a table so the scope of this rollup is auditable by reading it;
-- `src/__tests__/services/signal-census-contract.test.ts` asserts this list against a
-- hand-spelled 19-name list so the two cannot drift together and still pass.
--
-- The winner rule -- newest `release_retrieved_at`, tiebroken on the observation's own id -- is
-- the one `getPublishedClimateField` already applies, and it exists because this lane HAS
-- duplicates: ~47k (cell_id, signal_name, observed_at) keys carry two rows from overlapping
-- archive releases. Picking here rather than at each call site is what stops a reader silently
-- drawing whichever duplicate the plan happened to emit first.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_signal_cell_daily AS
WITH governed AS (
  SELECT
    observation.support_key::text AS support_key,
    observation.signal_name::text AS signal_name,
    observation.normalized_unit::text AS normalized_unit,
    observation.cell_id,
    (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
    observation.observed_at,
    observation.normalized_value,
    observation.coverage_fraction,
    observation.id AS observation_id,
    release.retrieved_at AS release_retrieved_at,
    source.allowed_client_exposure
  FROM agri.signal_observation AS observation
  JOIN agri.source_release AS release ON release.id = observation.source_release_id
  JOIN agri.data_source AS source ON source.id = release.data_source_id
  -- THE GOVERNED CONTRACT IS (signal_name, normalized_unit, lane), NOT signal_name ALONE, and the
  -- rollup has to join it the same way the governed views do or it serves a different population
  -- than the map paints.
  --
  --   * UNIT. `geo.soil_field_observation` (0016:50-66), `geo.soil_field_observation`'s vpd row
  --     (0019) and `geo.climate_field_observation` (0020:67-92) each join signal_name to an EXACT
  --     `normalized_unit`, and every app reader pins `reading.normalized_unit = $unit`. A rollup
  --     that only required `normalized_unit IS NOT NULL` would emit TWO rows per (cell, day) for a
  --     governed name the moment one off-contract unit lands -- the agent would report a second,
  --     differently-scaled value for a signal the map serves in exactly one unit.
  --   * LANE. `geo.climate_field_observation` additionally gates `source.key = 'nasa-power-daily'`,
  --     and 0020's own header records why `support_key` cannot substitute for it: "`surface` is a
  --     generic support the ERA5-Land writer also emits, so support_key does not discriminate
  --     between the two lanes." `precipitation`, `wind_speed` and `relative_humidity` are shared
  --     names; without this gate a non-NASA row under `support_key = 'surface'` is in the rollup
  --     the agent reads and absent from the view the map draws.
  --
  -- `required_source_key` is NULL for the ERA5-Land soil/vpd rows because 0016 and 0019 gate on
  -- the unit pair alone -- NULL here means "the governed view applies no lane gate", never
  -- "unknown". The 19 names are still spelled out, and
  -- `src/__tests__/services/signal-census-contract.test.ts` still asserts them against a
  -- hand-spelled list.
  JOIN (
    VALUES
      -- nasa-power-daily, support `surface`, grid `nasa-power-0.5-degree`, from 2022-08-06.
      ('air_temperature_mean'::text,        'C'::text,                      'nasa-power-daily'::text),
      ('air_temperature_max',               'C',                            'nasa-power-daily'),
      ('air_temperature_min',               'C',                            'nasa-power-daily'),
      ('dew_point_temperature',             'C',                            'nasa-power-daily'),
      ('precipitation',                     'mm/day',                       'nasa-power-daily'),
      ('relative_humidity',                 '%',                            'nasa-power-daily'),
      ('surface_shortwave_radiation',       'MJ/m^2/day',                   'nasa-power-daily'),
      ('wind_speed',                        'm/s',                          'nasa-power-daily'),
      -- nasa-power-daily soil-wetness pilot, same lane, opens 2022-08-06.
      ('soil_wetness_surface',              'fraction_of_saturation',       'nasa-power-daily'),
      ('soil_wetness_root_zone',            'fraction_of_saturation',       'nasa-power-daily'),
      ('soil_wetness_profile',              'fraction_of_saturation',       'nasa-power-daily'),
      -- open-meteo-era5-land-archive, support `era5-land-0.1deg`, grid `sentinel2-ndvi-0p25deg`,
      -- from 2022-04-30. 0016/0019 gate on the unit pair only, so no lane key is required here.
      ('soil_water_content_layer_1',        'm^3/m^3',                      NULL),
      ('soil_water_content_layer_2',        'm^3/m^3',                      NULL),
      ('soil_water_content_layer_3',        'm^3/m^3',                      NULL),
      ('soil_temperature_level_1',          'C',                            NULL),
      ('soil_temperature_level_2',          'C',                            NULL),
      ('soil_temperature_level_3',          'C',                            NULL),
      ('soil_temperature_level_4',          'C',                            NULL),
      ('vapor_pressure_deficit',            'kPa',                          NULL)
  ) AS governed(signal_name, normalized_unit, required_source_key)
    ON governed.signal_name = observation.signal_name
   AND governed.normalized_unit = observation.normalized_unit
   AND (governed.required_source_key IS NULL OR governed.required_source_key = source.key)
    -- The same three gates the governed views apply, applied HERE so no reader of this rollup can
    -- serve a rejected or imputed value as a measurement.
  WHERE observation.is_observed
    AND observation.quality_flag = 'accepted'
    AND observation.normalized_value IS NOT NULL
    -- `normalized_unit` is NULLABLE on the base table, and it is part of this rollup's UNIQUE
    -- INDEX. NULLs never compare equal, so a single NULL-unit row would be deleted and re-inserted
    -- by EVERY `REFRESH ... CONCURRENTLY` for the life of the relation -- a permanent, silent
    -- churn on the most expensive refresh in the design. The `governed` JOIN above already
    -- excludes it (an equality join never matches NULL), exactly as the two governed views do;
    -- this predicate is kept as a belt-and-braces statement of the index's own requirement, so a
    -- future edit that loosens the join cannot silently reintroduce the churn.
    AND observation.normalized_unit IS NOT NULL
)
SELECT
  support_key,
  signal_name,
  normalized_unit,
  cell_id,
  observed_day,
  (array_agg(normalized_value ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
    AS normalized_value,
  COUNT(*)::bigint AS observation_count,
  MIN(normalized_value) AS min_value,
  MAX(normalized_value) AS max_value,
  AVG(normalized_value) AS avg_value,
  MAX(observed_at) AS newest_observed_at,
  (array_agg(coverage_fraction ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
    AS coverage_fraction,
  (array_agg(allowed_client_exposure ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
    AS allowed_client_exposure
FROM governed
GROUP BY support_key, signal_name, normalized_unit, observed_day, cell_id
ORDER BY support_key, signal_name, normalized_unit, observed_day, cell_id
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_signal_cell_daily
  ON geo.mv_signal_cell_daily (support_key, signal_name, normalized_unit, cell_id, observed_day);
--> statement-breakpoint

-- The day-scoped field readers: one day, one signal, every cell. Leads with `observed_day` because
-- that is the equality they always carry and the unique index cannot serve it (it leads with
-- support_key).
CREATE INDEX IF NOT EXISTS ix_mv_signal_cell_daily_day_signal
  ON geo.mv_signal_cell_daily (observed_day, signal_name, support_key);
--> statement-breakpoint

-- The agent's cell-driven reads and its temporal-neighbour probes ("nearest covered day before
-- and after this one, for this cell"), which are a backwards and a forwards range scan from a
-- point in this index.
CREATE INDEX IF NOT EXISTS ix_mv_signal_cell_daily_cell_day
  ON geo.mv_signal_cell_daily (cell_id, observed_day);
--> statement-breakpoint

-- No GiST index, because there is no geometry column. Spatial questions are answered by driving
-- from `agri.spatial_cell`'s existing `ix_spatial_cell_centroid` / `ix_spatial_cell_geometry` and
-- joining into this rollup by `cell_id`.
COMMENT ON MATERIALIZED VIEW geo.mv_signal_cell_daily IS
  'One row per (support_key, signal_name, normalized_unit, cell_id, day) over the 19 governed '
  '(signal_name, normalized_unit, lane) triples -- the SAME population geo.soil_field_observation '
  'and geo.climate_field_observation serve, including the nasa-power-daily lane gate 0020 applies '
  'and the exact unit pairs 0016/0019/0020 join on. Physically clustered by its own ORDER BY so a '
  'serving read is a ~240 kB contiguous range instead of a scatter across an 11 GB heap. Carries '
  'no geometry: join agri.spatial_cell (~2,000 rows) at read time.';
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 3. THE SMALL DICTIONARIES
-- ---------------------------------------------------------------------------------------------

-- 3a. `geo.mv_drought_release_index` -- one row per published USDM valid_date, ~208 rows.
--
-- Retires `resolveDroughtRelease`'s unbounded full scan of `geo.drought_areas`, which was issued
-- by BOTH drought readers and again inside `assembleRegionalContext`. NEVER SELECTS `geom`, for
-- the 495 MB reason recorded on `mv_drought_observation_day`.
--
-- `earliest_valid_date` and `latest_valid_date` are carried on every row -- a redundancy that is
-- deliberate. `resolveDroughtRelease` must distinguish "the record starts on day X, and your date
-- precedes it" from "nothing was ever published", and those are different sentences about
-- different warehouse states. Carrying the record's own bounds on the covering row answers both
-- in one index probe instead of a second query against an empty result.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_drought_release_index AS
WITH release AS (
  SELECT
    d.valid_date::date AS valid_date,
    COUNT(*)::bigint AS class_count,
    MAX(d.ingested_at) AS newest_ingested_at
  FROM geo.drought_areas AS d
  GROUP BY d.valid_date
)
SELECT
  valid_date,
  LAG(valid_date) OVER (ORDER BY valid_date) AS prev_valid_date,
  LEAD(valid_date) OVER (ORDER BY valid_date) AS next_valid_date,
  class_count,
  MIN(valid_date) OVER () AS earliest_valid_date,
  MAX(valid_date) OVER () AS latest_valid_date,
  newest_ingested_at
FROM release
ORDER BY valid_date
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_drought_release_index
  ON geo.mv_drought_release_index (valid_date);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_drought_release_index IS
  'One row per USDM valid_date with its neighbours and the record bounds, ~208 rows. Two index '
  'probes replace a full scan of a table hiding 495 MB of TOAST. Never projects geom.';
--> statement-breakpoint

-- 3b. `geo.mv_layer_feature_stats` -- 11 rows.
--
-- LEFT JOIN, not JOIN: a layer with zero published features must still get a row, because
-- `getFeatureCountByLayer` and `getLayerFeatureStats` join `geo.layers` to this relation and an
-- absent row would delete the layer from the panel entirely -- reporting a layer that exists and
-- is empty as a layer that does not exist. `interventions` is exactly that case today.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_layer_feature_stats AS
SELECT
  l.id AS layer_id,
  l.name AS layer_name,
  COUNT(f.id) FILTER (WHERE f.status = 'published')::bigint AS published_count,
  MAX(f.created_at) AS newest_created_at,
  MAX(f.updated_at) AS newest_updated_at
FROM geo.layers AS l
LEFT JOIN geo.features AS f ON f.layer_id = l.id
GROUP BY l.id, l.name
ORDER BY l.id
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_layer_feature_stats
  ON geo.mv_layer_feature_stats (layer_id);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_layer_feature_stats IS
  'Published feature count and newest write time per layer, 11 rows. Retires '
  'getFeatureCountByLayer, getSystemStats and layerStats. LEFT JOIN so an empty layer still has a '
  'row and is reported as empty rather than as absent.';
--> statement-breakpoint

-- 3c. `geo.mv_layer_hourly_activity` -- (layer_id, hour_bucket) over the trailing 168 hours.
--
-- 168 is not arbitrary: it is the ceiling the `analytics.getRecentActivity` zod schema already
-- enforces, so ANY N the router accepts is answerable as a SUM over an indexed range of at most
-- 168 buckets per layer. The predicate this replaces was `created_at >= now() - N hours` with no
-- index that could serve it -- `idx_features_layer_created_at` leads with `layer_id`, so a
-- created_at-only filter fell through to a scan of every published row.
--
-- REFRESH-CADENCE HAZARD, same class as `mv_drought_observation_day`: the window is relative to
-- `now()`, so it goes wrong by one bucket per hour even when nothing is written. A watermark of
-- `max(geo.features.updated_at)` alone will skip a refresh this view needed; the lane's watermark
-- for this view MUST carry the current hour. Its 1 h max-staleness bound is a backstop, not the
-- mechanism.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_layer_hourly_activity AS
SELECT
  l.id AS layer_id,
  date_trunc('hour', f.created_at) AS hour_bucket,
  COUNT(*)::bigint AS feature_count
FROM geo.layers AS l
JOIN geo.features AS f ON f.layer_id = l.id
WHERE f.status = 'published'
  AND f.created_at >= date_trunc('hour', now()) - interval '168 hours'
GROUP BY l.id, date_trunc('hour', f.created_at)
ORDER BY l.id, date_trunc('hour', f.created_at)
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_layer_hourly_activity
  ON geo.mv_layer_hourly_activity (layer_id, hour_bucket);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_layer_hourly_activity IS
  'Published features created per layer per hour over the trailing 168 hours -- the exact ceiling '
  'the analytics router enforces, so every accepted N is a bounded SUM. Window is relative to '
  'now(), so its refresh watermark must include the current hour.';
--> statement-breakpoint

-- 3d. `geo.mv_soil_survey_grid` -- the counted lattice, for viewports too wide to union honestly.
--
-- INTENDED to retire `readSummaryFeatures`' `mode() WITHIN GROUP` over a 200,000-row input. IT
-- DOES NOT YET: `readSummaryFeatures` is NOT repointed at this relation, and says so in
-- src/lib/server/services/usda-soil.ts. `soilSummaryCellDegrees` walks an UNBOUNDED doubling
-- ladder and hands the chosen step to the client as `cellDegrees`; pinning it to the 13 tiers
-- below is the prerequisite, and it is a read-path change with a client-visible contract in it.
-- Both existing reads are bounded today (20,000 / 200,000 input rows), so nothing is on fire.
--
-- ZOOM_TIER ENCODING, which the read path must use verbatim: `zoom_tier` is the integer k in
-- `step = SOIL_SURVEY_CELL_DEGREES * 2^k`, with SOIL_SURVEY_CELL_DEGREES = 0.125. That is exactly
-- the ladder `soilSummaryCellDegrees` walks (it starts at 0.125 and doubles until the viewport's
-- span fits in MAX_SOIL_SUMMARY_CELLS_PER_SIDE cells), so a reader converts with
-- `k = log2(step / 0.125)` and needs no new arithmetic. `cell_degrees` is carried alongside so the
-- reader can emit the `cellDegrees` property without recomputing it, and so a row is auditable
-- without knowing the encoding.
--
-- k runs 0..12, i.e. 0.125 degrees to 512 degrees, which covers every viewport from a city block
-- to the whole globe. Doubling keeps every step exactly representable in binary floating point, so
-- `floor(lon / step)` is exact at every tier and a delineation can never land in two lattice cells
-- or in none -- the same property `soilSurveyCellKey` relies on.
--
-- Grouped on the delineation's CENTROID rather than its extent, so each delineation is counted
-- exactly once. `geo.geometry.centroid` is preferred where the feature carries a geometry-dimension
-- row (precomputed from the validated geometry at ingest, so it is both cheaper and the exact same
-- point the warehouse stored) and recomputed only for a feature that has none.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_soil_survey_grid AS
WITH delineation AS (
  SELECT
    COALESCE(f.properties ->> 'drainageClass', 'unknown') AS drainage_class,
    CASE
      WHEN jsonb_typeof(f.properties -> 'hydric') = 'boolean'
        THEN (f.properties ->> 'hydric')::boolean
    END AS hydric,
    COALESCE(g.centroid, ST_Centroid(f.geom)) AS centroid
  FROM geo.features AS f
  JOIN geo.layers AS l ON l.id = f.layer_id
  LEFT JOIN geo.geometry AS g ON g.geometry_id = f.geometry_id
  WHERE l.name = 'soil-survey'
    AND f.status = 'published'
    AND f.geom IS NOT NULL
),
-- `cell_col` and `cell_row` are part of this rollup's UNIQUE INDEX, and NULLs never compare equal,
-- so one undatable row would be deleted and re-inserted by EVERY `REFRESH ... CONCURRENTLY`
-- forever. `ST_Centroid` of an empty or degenerate geometry returns `POINT EMPTY`, `ST_X` of that
-- returns NULL, and `floor(NULL)` is NULL -- so the emptiness has to be filtered before the
-- lattice arithmetic, not after. A delineation with no locatable centroid is not counted anywhere;
-- it has no position, so no lattice cell is the honest answer for it.
located AS (
  SELECT drainage_class, hydric, centroid
  FROM delineation
  WHERE centroid IS NOT NULL
    AND NOT ST_IsEmpty(centroid)
),
tier AS (
  SELECT
    k AS zoom_tier,
    (0.125 * power(2::double precision, k))::double precision AS cell_degrees
  FROM generate_series(0, 12) AS k
)
SELECT
  tier.zoom_tier,
  tier.cell_degrees,
  floor(ST_X(located.centroid) / tier.cell_degrees)::bigint AS cell_col,
  floor(ST_Y(located.centroid) / tier.cell_degrees)::bigint AS cell_row,
  mode() WITHIN GROUP (ORDER BY located.drainage_class) AS drainage_class,
  COUNT(*)::bigint AS map_unit_count,
  COUNT(*) FILTER (WHERE located.hydric)::bigint AS hydric_count,
  COUNT(*) FILTER (WHERE located.hydric IS NOT NULL)::bigint AS rated_count
FROM located
CROSS JOIN tier
GROUP BY tier.zoom_tier, tier.cell_degrees,
         floor(ST_X(located.centroid) / tier.cell_degrees),
         floor(ST_Y(located.centroid) / tier.cell_degrees)
ORDER BY tier.zoom_tier,
         floor(ST_X(located.centroid) / tier.cell_degrees),
         floor(ST_Y(located.centroid) / tier.cell_degrees)
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_soil_survey_grid
  ON geo.mv_soil_survey_grid (zoom_tier, cell_col, cell_row);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_soil_survey_grid IS
  'SSURGO delineation counts per lattice cell at 13 power-of-two zoom tiers (zoom_tier = k where '
  'step = 0.125 * 2^k, the ladder soilSummaryCellDegrees walks). NOT YET READ BY ANYTHING: '
  'readSummaryFeatures still counts its own input, because soilSummaryCellDegrees picks its step '
  'off an UNBOUNDED doubling ladder and returns it to the client as cellDegrees, so repointing it '
  'requires pinning that ladder to this enumerated tier set first. Built here so the reader '
  'change is a read-path edit rather than a migration; until that reader lands this relation is '
  'cost with no benefit, and it should be dropped rather than kept if the reader is abandoned.';
--> statement-breakpoint

-- 3e. `geo.mv_soil_survey_union` -- the dissolved drainage-class shapes.
--
-- INTENDED to retire `readAggregatedFeatures`' `ST_Union` over up to 20,000 polygons, which is the
-- LARGEST TRANSIENT ALLOCATION any request in this codebase can make. IT DOES NOT YET:
-- `readAggregatedFeatures` is NOT repointed at this relation, and says so in
-- src/lib/server/services/usda-soil.ts. That reader unions the REQUESTING BBOX at a caller-chosen
-- tolerance; answering it from a global dissolve means ST_Intersection'ing the dissolve back to
-- the viewport, which is worse than the union it replaces. Repointing it is a redesign.
-- Moving it here does not make the
-- union cheap; it makes it happen once per refresh (7 d max staleness) instead of once per
-- viewport pan, in a session with its own tuned `work_mem` rather than on a request path.
--
-- TWO TIERS, matching the two simplify tolerances `getSoilSurvey` chooses between:
-- `regional-average` (0.0015) and `coarse-average` (0.005). The `detail` tier draws real
-- delineations and never unions, so it has no row here.
--
-- HIERARCHICAL UNION, and this is the part that keeps the refresh affordable. Unioning ~200,000
-- delineations in one `ST_Union` call is minutes of CPU and a peak allocation nobody on a 3 GB
-- cgroup should attempt; unioning them per one-degree tile and then unioning ~100 tiles is the
-- same answer for a fraction of the peak. This is the lesson 0023 recorded when it built the
-- watershed rollup 12 -> 10 -> 8 -> 6 -> 4 rather than each level from the base.
--
-- The snap/MakeValid/CollectionExtract repair chain is 0023's, for 0023's reason: published
-- polygon sets are not all OGC-valid, and unioning them raw fails with "unable to assign free hole
-- to a shell". Snapping to a ~0.1 m grid closes near-coincident vertices, MakeValid repairs what
-- remains, and CollectionExtract keeps only the polygonal part -- a repair can hand back a
-- GeometryCollection with stray lines, which ST_Union then refuses.
--
-- KNOWN HAZARD, stated rather than hidden: at grain (zoom_tier, drainage_class) each row holds one
-- dissolved multipolygon covering the whole surveyed extent, so this relation is a small number of
-- LARGE TOASTed geometries. A reader must clip with `geom && ST_MakeEnvelope(...)` before
-- `ST_Intersection`, and must not `SELECT geom` for a viewport it has not first bounded. If
-- measurement shows the detoast dominates, the fix is to add the one-degree tile columns below to
-- the grain -- the tiled CTE already computes them -- which changes the unique index and is a
-- follow-up migration, not an edit to this one.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_soil_survey_union AS
WITH delineation AS (
  SELECT
    COALESCE(f.properties ->> 'drainageClass', 'unknown') AS drainage_class,
    CASE
      WHEN jsonb_typeof(f.properties -> 'hydric') = 'boolean'
        THEN (f.properties ->> 'hydric')::boolean
    END AS hydric,
    ST_MakeValid(ST_SnapToGrid(f.geom, 0.000001)) AS geom
  FROM geo.features AS f
  JOIN geo.layers AS l ON l.id = f.layer_id
  WHERE l.name = 'soil-survey'
    AND f.status = 'published'
    AND f.geom IS NOT NULL
),
-- The snap-and-repair above can collapse a sliver to an EMPTY geometry, whose centroid is
-- `POINT EMPTY` and whose `ST_X` is NULL -- which would open a NULL one-degree tile group below.
-- That group is harmless to the unique index (the final grain is zoom_tier + drainage_class, both
-- non-null) but it makes the tiling do work for inputs that contribute no area, so the collapsed
-- rows are dropped here where the cost is one comparison.
repaired AS (
  SELECT drainage_class, hydric, geom
  FROM delineation
  WHERE geom IS NOT NULL
    AND NOT ST_IsEmpty(geom)
),
tier AS (
  SELECT * FROM (
    VALUES
      ('regional-average'::text, 0.0015::double precision),
      ('coarse-average',         0.005)
  ) AS t(zoom_tier, simplify_tolerance_degrees)
),
tiled AS (
  SELECT
    tier.zoom_tier,
    tier.simplify_tolerance_degrees,
    repaired.drainage_class,
    floor(ST_X(ST_Centroid(repaired.geom)))::integer AS tile_x,
    floor(ST_Y(ST_Centroid(repaired.geom)))::integer AS tile_y,
    ST_CollectionExtract(
      ST_MakeValid(
        ST_Union(
          ST_SimplifyPreserveTopology(repaired.geom, tier.simplify_tolerance_degrees)
        )
      ),
      3
    ) AS geom,
    COUNT(*)::bigint AS map_unit_count,
    COUNT(*) FILTER (WHERE repaired.hydric)::bigint AS hydric_count,
    COUNT(*) FILTER (WHERE repaired.hydric IS NOT NULL)::bigint AS rated_count
  FROM repaired
  CROSS JOIN tier
  GROUP BY tier.zoom_tier, tier.simplify_tolerance_degrees, repaired.drainage_class,
           floor(ST_X(ST_Centroid(repaired.geom))),
           floor(ST_Y(ST_Centroid(repaired.geom)))
)
SELECT
  zoom_tier,
  simplify_tolerance_degrees,
  drainage_class,
  ST_CollectionExtract(ST_MakeValid(ST_Union(geom)), 3) AS geom,
  SUM(map_unit_count)::bigint AS map_unit_count,
  SUM(hydric_count)::bigint AS hydric_count,
  SUM(rated_count)::bigint AS rated_count
FROM tiled
GROUP BY zoom_tier, simplify_tolerance_degrees, drainage_class
ORDER BY zoom_tier, drainage_class
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_soil_survey_union
  ON geo.mv_soil_survey_union (zoom_tier, drainage_class);
--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ix_mv_soil_survey_union_geom
  ON geo.mv_soil_survey_union USING GIST (geom);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_soil_survey_union IS
  'Dissolved SSURGO drainage-class shapes at the two averaged zoom tiers. NOT YET READ BY '
  'ANYTHING: readAggregatedFeatures unions the requesting bbox at a caller-chosen tolerance, and '
  'a global dissolve would have to be ST_Intersection''d back -- strictly worse -- so repointing '
  'it is a redesign, not an edit. Until that reader lands this relation is cost with no benefit. '
  'Built hierarchically -- '
  'per one-degree tile, then across tiles -- so the refresh peak stays inside a 3 GB cgroup. Each '
  'row is one large TOASTed geometry: clip with && before ST_Intersection, never project geom for '
  'an unbounded viewport.';
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 4. THE INGEST-TIME REFRESH DOOR
-- ---------------------------------------------------------------------------------------------
-- THE SCHEDULED PATH IS THE `matview-refresh` LANE IN THE JOBS PULSE, NOT THIS FUNCTION. Every one
-- of the nine matviews above is registered there with a watermark query, a minimum interval and a
-- maximum staleness, and that registration -- not this function -- is what satisfies the rule that
-- no matview is ever left unscheduled. `agri.mv_forecast_ml_daily_serving` exists today with
-- `relispopulated = false` and a refresher nothing ever called; that is the bug this rule prevents.
--
-- This function is the OTHER door: the one an ingest verb uses to refresh a rollup the instant it
-- lands new data, instead of waiting out the lane's interval. It is exactly the role
-- `geo.refresh_watershed_rollup()` plays for `agri-cli ingest-watersheds` (drizzle/0023:106-108),
-- generalised to nine views so each caller does not re-implement the unpopulated guard.
--
-- ONE function rather than nine wrappers: nine near-identical bodies is nine places for the guard
-- to be forgotten, and the allow-list below is what makes a single parameterised body safe.
--
-- WHY THE ALLOW-LIST. `format('%I.%I', ...)` already prevents SQL injection through the identifier,
-- but it does not prevent this function becoming a general-purpose "refresh anything" door that a
-- future caller points at a 26 GB relation. The list is the contract: a name not on it is refused
-- by name, loudly.
--
-- WHY THE `relispopulated` BRANCH. `REFRESH MATERIALIZED VIEW CONCURRENTLY` raises "cannot refresh
-- materialized view ... concurrently" on a view that has never been populated -- and every view in
-- this file is created `WITH NO DATA`, so every one of them starts life in that state. Falling back
-- to a plain refresh with a WARNING makes the unpopulated case self-healing rather than permanent.
-- The plain refresh takes ACCESS EXCLUSIVE, which is safe exactly once, before any reader exists.
--
-- `REFRESH ... CONCURRENTLY` IS legal inside a transaction and inside a plpgsql function --
-- `geo.refresh_watershed_rollup()` has proved it since 0023. Do not confuse it with
-- `CREATE INDEX CONCURRENTLY`, which is not.
CREATE OR REPLACE FUNCTION geo.refresh_preaggregate(target_view text)
RETURNS bigint
LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS $$
DECLARE
  allowed CONSTANT text[] := ARRAY[
    'geo.mv_feature_observation_day',
    'geo.mv_signal_observation_day',
    'geo.mv_drought_observation_day',
    'geo.mv_signal_cell_daily',
    'geo.mv_drought_release_index',
    'geo.mv_layer_feature_stats',
    'geo.mv_layer_hourly_activity',
    'geo.mv_soil_survey_grid',
    'geo.mv_soil_survey_union'
  ];
  view_oid oid;
  is_populated boolean;
  row_total bigint;
BEGIN
  IF target_view <> ALL (allowed) THEN
    RAISE EXCEPTION
      'geo.refresh_preaggregate refuses %: not one of the nine pre-aggregation matviews', target_view
      USING HINT = 'Add the view to drizzle/0029 and to the matview-refresh lane before refreshing it here.';
  END IF;

  view_oid := to_regclass(target_view);
  IF view_oid IS NULL THEN
    RAISE EXCEPTION '% is on the allow-list but does not exist in this database', target_view;
  END IF;

  SELECT relispopulated INTO is_populated FROM pg_class WHERE oid = view_oid;

  IF is_populated THEN
    EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY %s', target_view::regclass::text);
  ELSE
    RAISE WARNING
      '% has never been populated; refreshing NON-concurrently (ACCESS EXCLUSIVE) to heal it', target_view;
    EXECUTE format('REFRESH MATERIALIZED VIEW %s', target_view::regclass::text);
  END IF;

  EXECUTE format('SELECT count(*) FROM %s', target_view::regclass::text) INTO row_total;
  RETURN row_total;
END;
$$;
--> statement-breakpoint

COMMENT ON FUNCTION geo.refresh_preaggregate(text) IS
  'The ingest-time refresh door for the nine pre-aggregation matviews: allow-listed by name, '
  'CONCURRENTLY when the view is populated and plain-with-a-WARNING when it is not. The SCHEDULED '
  'path is the matview-refresh lane in the jobs pulse; this is for a verb that just landed data '
  'and should not wait out the interval. Returns the view''s row count after the refresh.';
