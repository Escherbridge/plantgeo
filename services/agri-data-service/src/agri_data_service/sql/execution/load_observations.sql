-- Purpose: turn one batch of raw NDVI features into governed daily cell-mean observations attached
--          to their series and source release, reporting the observations actually written.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: layer_name (text) -- the geo layer the NDVI features live on, always 'vegetation';
--         cell_keys (text[]) -- one batch of unprefixed cell keys to materialise; cutoff_day (date)
--         -- the publisher-named day the corpus stops at, inclusive; source_release_id (uuid) -- the
--         governed release every written observation belongs to; day_bucket_rule (text) -- the name
--         of the rule used to assign an observation to a day; grid_name (text) -- the analysis grid,
--         also the cell-key prefix; metric_name (text) -- which metric's series to attach to;
--         transform_version (text) -- which transform's series to attach to.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per observation this statement actually inserted, holding its new id.
-- The caller counts those rows, so the count means "newly written", not "in existence": an
-- observation already materialised by an earlier pass produces no row.
--
-- How this query works, clause by clause:
--
--   WITH daily AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one performs the whole transform: it reduces raw features to exactly one
--     row per cell per observed day, carrying that day's mean NDVI plus the evidence describing how
--     the mean was reached. Everything after it is bookkeeping and lineage.
--
--   feature.properties->>'cellKey'
--     The features table keeps its per-feature attributes in one JSON column; the arrow-with-two-
--     angle-brackets operator reads one named field out of it as ordinary text.
--
--   substring(feature.properties->>'observedAt', 1, 10)::date
--     The observation timestamp is an ISO 8601 string whose first ten characters are the calendar
--     date. Slicing those characters and casting them to a date preserves the publisher's own naming
--     of the day; converting the full timestamp would re-bucket observations by session time zone
--     and move them across day boundaries, silently changing the history a forecast trains on. The
--     rule's name is stored on every row so a reader never has to infer this.
--
--   ANY(CAST(cell_keys AS text[]))
--     "equals any element of this array" -- the set-membership form of an equality test, which is how
--     one statement covers a whole batch of keys. The cast pins the parameter's type: a bare bind
--     parameter has none of its own and the database will not guess which kind of array it received.
--
--   feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
--     Resolves the layer NAME to its id via an uncorrelated subquery rather than joining geo.layers
--     onto the scan. geo.features is partitioned by layer_id: a join filtered on layer.name only
--     restricts the layers side, so the planner can never fold it into a partition-pruning constant
--     for the features side and must scan every partition. A scalar subquery on the (unpartitioned,
--     11-row) layers table runs once as an init-plan and hands the Append node one concrete layer_id
--     before it opens a single partition.
--
--   avg / count / max / sum / array_agg(DISTINCT ...) inside the CTE
--     The aggregates that describe one cell-day: its mean NDVI, how many source rows produced it, the
--     latest moment any of those rows became available, the total pixels sampled, the worst cloud
--     cover seen, and the distinct scenes involved. DISTINCT inside array_agg lists each scene once
--     however many features it contributed. Keeping the evidence alongside the value is what lets a
--     later reader judge a number rather than merely consume it.
--
--   max(feature.created_at) AS data_available_at
--     The honest availability time: the latest moment at which the last contributing row existed
--     locally. A forecast may only use observations that were already available at its own as-of
--     time, so understating this would leak future knowledge into a backtest.
--
--   GROUP BY 1, 2
--     Collapses rows agreeing on the first two selected expressions -- the cell key and the observed
--     day. The numbers are SELECT-list positions, shorthand for repeating those expressions. Every
--     other column must therefore be an aggregate: a group of features has no single ndvi, only an
--     average.
--
--   INSERT INTO agri.forecast_observation (...) SELECT ... FROM daily INNER JOIN ... INNER JOIN ...
--     The rows to insert come from a query rather than from literal VALUES, so the whole batch is
--     written in one statement.
--
--   INNER JOIN agri.spatial_cell AS cell ON cell.cell_key = CAST(grid_name AS text) || ':' || daily.entity_key
--     Resolves a bare entity key to the registered spatial cell by rebuilding the grid-qualified key
--     the double-pipe operator joins. The cast pins the parameter to text so the join expression has
--     a resolvable type. INNER is the safety property: a cell-day whose cell was never registered
--     simply produces no row, so an observation can never be written without a governed cell behind
--     it. A LEFT join would have written it with an empty cell instead.
--
--   INNER JOIN agri.forecast_series AS series ON series.spatial_cell_id = cell.id
--        AND series.metric_name = CAST(metric_name AS varchar)
--        AND series.source_transform_version = CAST(transform_version AS varchar)
--     Finds the one series that owns this cell for this metric under this transform. All three
--     conditions are needed: a cell can carry several series, and matching on the cell alone would
--     attach NDVI values to whatever series happened to exist. The two casts pin the parameters to
--     the column type they are compared against, so the database compares like with like instead of
--     resolving an untyped parameter by guesswork. Again INNER, so an unregistered series yields no
--     row rather than an orphaned observation.
--
--   daily.observed_day::timestamptz and (daily.observed_day + 1)::timestamptz
--     The value's validity window, stored half-open: it starts at midnight on the observed day and
--     ends at midnight on the next. Half-open windows tile without overlapping, so consecutive days
--     never both claim the same instant.
--
--   'accepted'  (the quality_flag column)
--     A literal: everything reaching this statement has already passed the lane's quality gate. A
--     rejected observation is not written with a flag, it is not written at all.
--
--   concat_ws(':', daily.entity_key, daily.observed_day::text)
--     The source event key -- a stable name for "this cell on this day". concat_ws joins its
--     arguments with the given separator ("with separator" is what the ws stands for). It is what
--     lets a re-run recognise a fact it has already recorded.
--
--   encode(digest(concat_ws('|', 'sentinel2_ndvi_daily_cell_mean_v1', ...), 'sha256'), 'hex')
--     The per-observation fingerprint. concat_ws joins the transform's name, the identity of the
--     cell-day, the computed value, the number of source rows and the contributing scene ids into one
--     string; digest hashes it with SHA-256 into raw bytes; encode renders those bytes as the usual
--     lowercase hexadecimal string. Including the transform name namespaces the checksum, so a
--     different transform over identical inputs can never produce a colliding fingerprint. Any change
--     to the value or to the evidence behind it changes this checksum.
--
--   array_to_string(daily.scene_ids, ',')
--     Flattens the collected scene list into one string so it can take part in the hashed material;
--     an array cannot be hashed directly.
--
--   jsonb_build_object('dayBucketRule', CAST(day_bucket_rule AS text), ...)
--     Assembles the metadata document from alternating names and values, rather than from a string
--     the caller pasted together -- so the values keep their real types and no escaping bug can
--     produce malformed JSON. The cast pins the parameter's type, which the database will not guess.
--     to_jsonb turns the scene array into a JSON array so it is stored as a list rather than as text.
--
--   ON CONFLICT DO NOTHING
--     Idempotency. Written without naming columns or a constraint, meaning "if this row would violate
--     ANY uniqueness constraint on the table, skip it silently". Re-materialising a batch therefore
--     rewrites nothing instead of failing on the uniqueness constraint and aborting the surrounding
--     transaction. DO NOTHING and not DO UPDATE: a governed observation is evidence, and a later pass
--     must not silently restate it.
--
--   RETURNING id
--     Asks the INSERT to hand back a row for each row it actually wrote. Skipped conflicts are not
--     written and so are not returned, which is what makes the caller's count mean "newly written".
WITH daily AS (
    SELECT
        feature.properties->>'cellKey' AS entity_key,
        substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day,
        avg((feature.properties->>'ndvi')::double precision) AS metric_value,
        count(*)::integer AS source_row_count,
        max(feature.created_at) AS data_available_at,
        sum((feature.properties->>'sampleCount')::integer)::integer AS pixel_sample_count,
        max((feature.properties->>'cloudCover')::double precision) AS max_cloud_cover,
        array_agg(DISTINCT feature.properties->>'sceneId') AS scene_ids
    FROM geo.features AS feature
    WHERE feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
      AND feature.properties->>'cellKey' = ANY(CAST(:cell_keys AS text[]))
      AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
    GROUP BY 1, 2
)
INSERT INTO agri.forecast_observation (
    series_id, source_release_id, observed_at, valid_from, valid_to,
    data_available_at, metric_value, quality_flag, source_event_key,
    observation_checksum, metadata_json
)
SELECT
    series.id,
    :source_release_id,
    daily.observed_day::timestamptz,
    daily.observed_day::timestamptz,
    (daily.observed_day + 1)::timestamptz,
    daily.data_available_at,
    daily.metric_value,
    'accepted',
    concat_ws(':', daily.entity_key, daily.observed_day::text),
    encode(
        digest(
            concat_ws(
                '|',
                'sentinel2_ndvi_daily_cell_mean_v1',
                daily.entity_key,
                daily.observed_day::text,
                daily.metric_value::text,
                daily.source_row_count::text,
                array_to_string(daily.scene_ids, ',')
            ),
            'sha256'
        ),
        'hex'
    ),
    jsonb_build_object(
        'dayBucketRule', CAST(:day_bucket_rule AS text),
        'sourceRowCount', daily.source_row_count,
        'pixelSampleCount', daily.pixel_sample_count,
        'maxSceneCloudCoverPercent', daily.max_cloud_cover,
        'sceneIds', to_jsonb(daily.scene_ids)
    )
FROM daily
INNER JOIN agri.spatial_cell AS cell
    ON cell.cell_key = CAST(:grid_name AS text) || ':' || daily.entity_key
INNER JOIN agri.forecast_series AS series
    ON series.spatial_cell_id = cell.id
   AND series.metric_name = CAST(:metric_name AS varchar)
   AND series.source_transform_version = CAST(:transform_version AS varchar)
ON CONFLICT DO NOTHING
RETURNING id
