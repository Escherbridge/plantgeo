-- feature_observed_days
-- Purpose: for every feature layer, one row per calendar day that layer actually holds observations
--          for, with how many rows that day carries. This is the raw material for the completeness
--          report and for the day axis the map's time slider offers.
-- Loaded by: agri_data_service.ingest.validation
-- Params: published_status (text) -- the one `geo.features.status` value the map serves,
--         row_limit (int) -- a hard cap on returned rows; the Python side refuses a result that
--         reaches the cap rather than reasoning about a truncated day set.
--
-- The first line above is a dispatch marker the unit tests match statements on. It stays first and
-- stays spelled as it is -- see "Marker protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: one row per (layer, day) pair, ordered by layer then day, each row carrying the
-- layer's name, the day, and the number of published features observed on it.
--
-- WHY THE THREE FILTERS ARE EXACTLY THESE THREE: this mirrors the `observed` step of the TypeScript
-- read model's readObservationWindows -- published rows only, geometry-linked rows only, and only a
-- publisher-named day that `geo.feature_observation_day` could actually parse. Anything looser would
-- report days back to the caller that the slider will never offer, and the report would call a layer
-- complete over a span the user cannot scrub through.
--
-- How this query works, clause by clause:
--
--   FROM geo.layers AS layers JOIN geo.features AS features ON features.layer_id = layers.id
--     A join stitches two tables together row by row on a matching condition -- here, each feature row
--     is paired with the layer row it belongs to, so the layer's human-readable name is available
--     beside the feature. A plain JOIN (an "inner" join) keeps only pairs that match, which is what is
--     wanted: a feature with no layer is not a thing this schema can hold, and an empty layer has no
--     observed days to report by definition.
--
--   geo.feature_observation_day(features.properties)
--     A database function this service owns. It digs the publisher's own observation timestamp out of
--     the feature's JSON payload and reduces it to a UTC calendar day, answering NULL when the payload
--     carries no parseable one. Calling it rather than reading a column is the point: the same rule
--     that dates a feature for the map dates it for this report.
--
--   WHERE features.status = published_status
--     Only rows the map actually serves. Draft or superseded rows are real rows in the table but
--     nobody can see them, so counting them would overstate coverage.
--
--   AND features.geometry_id IS NOT NULL
--     Only rows linked into the geometry dimension. An unlinked row has no shape to draw and the
--     serving path skips it. `IS NOT NULL` rather than `<> NULL` because in SQL a comparison against
--     NULL is itself NULL -- neither true nor false -- so `IS` is the only way to test for it.
--
--   AND geo.feature_observation_day(features.properties) IS NOT NULL
--     Only rows whose day could be parsed. An undated row is counted elsewhere in the report as a
--     validity failure; it must not silently land on some default day here.
--
--   GROUP BY layers.name, geo.feature_observation_day(features.properties)
--     GROUP BY collapses many rows into one row per distinct combination of the listed expressions --
--     here, one row per layer per day. Once rows are collapsed there is no longer a single "the
--     feature" to select, so every other selected column must be an aggregate that summarises the
--     group. That is what `count(*)` is: the number of rows that fell into this group.
--
--   ORDER BY layers.name, geo.feature_observation_day(features.properties)
--     A stable, total order. It also makes the LIMIT below deterministic -- without an order, a capped
--     result would be an arbitrary sample rather than the earliest days.
--
--   LIMIT row_limit
--     The cap. It exists so a runaway layer cannot pull an unbounded result into memory; the Python
--     side treats hitting it as an error, not as an answer.
SELECT layers.name                                            AS stream,
       geo.feature_observation_day(features.properties)       AS observed_day,
       count(*)                                               AS observation_count
  FROM geo.layers AS layers
  JOIN geo.features AS features
    ON features.layer_id = layers.id
 WHERE features.status = :published_status
   AND features.geometry_id IS NOT NULL
   AND geo.feature_observation_day(features.properties) IS NOT NULL
 GROUP BY layers.name, geo.feature_observation_day(features.properties)
 ORDER BY layers.name, geo.feature_observation_day(features.properties)
 LIMIT :row_limit
