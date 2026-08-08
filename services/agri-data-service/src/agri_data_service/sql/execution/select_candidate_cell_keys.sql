-- Purpose: pick a deterministic, spatially spread sample of vegetation cell keys that have enough
--          observed days behind them to be worth forecasting.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: layer_name (text) -- the geo layer the NDVI features live on, always 'vegetation';
--         cutoff_day (date) -- the publisher-named day the caller is pinned to, inclusive; nothing
--         observed after it may influence the choice; min_observed_days (int) -- how many distinct
--         observed days a cell must have to qualify; cell_limit (int) -- how many cells to return.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one column, one row per selected cell -- the cell's entity key. The list is
-- reproducible: the same database contents and the same parameters always yield the same cells in
-- the same order, which is what makes a forecasting run repeatable.
--
-- How this query works, clause by clause:
--
--   WITH observed AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one reduces the raw feature rows to the distinct (cell, day) pairs that
--     actually have an observation. It exists because the question below is "how many DAYS does
--     this cell have", not "how many rows"; a satellite pass can drop several features onto the same
--     cell on the same day, and counting rows would overstate a cell's real history.
--
--   feature.properties->>'cellKey'
--     The features table stores its per-feature attributes in one JSON column. The arrow-with-two-
--     angle-brackets operator reads one named field out of that JSON and hands it back as ordinary
--     text. Every reference to a satellite attribute in this file goes through it.
--
--   substring(feature.properties->>'observedAt', 1, 10)::date
--     The observation timestamp is stored as an ISO 8601 string; its first ten characters are the
--     calendar date. Taking those ten characters and casting them to a date is how the publisher's
--     own naming of the day is preserved -- converting the full timestamp to a date instead would
--     re-bucket observations by whatever time zone the session happened to be in, which would move
--     observations across day boundaries and silently change the history.
--
--   INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
--     Features carry only a layer id, so they are joined to the layer table purely to filter by
--     layer name. INNER means a feature whose layer is missing contributes nothing, which is the
--     correct behaviour: an unattributable feature has no place in a governed sample.
--
--   GROUP BY 1, 2
--     Collapses rows that agree on the first two selected expressions -- here the cell key and the
--     observed day. The numbers are positions in the SELECT list, a shorthand for repeating those
--     two long expressions. Since the CTE selects nothing else, the effect is exactly
--     de-duplication: many feature rows for one cell-day become one row.
--
--   GROUP BY observed.entity_key ... HAVING count(*) >= min_observed_days
--     The outer query groups again, this time collapsing all of a cell's days into one row per cell,
--     so count(*) is now that cell's number of distinct observed days. HAVING filters those grouped
--     rows -- it is WHERE for groups. WHERE could not express this, because WHERE is applied before
--     the grouping happens and so cannot see a group's count. Cells with a thin history are dropped
--     here rather than being forecast from too little evidence.
--
--   ORDER BY md5(observed.entity_key)
--     A deliberately arbitrary but completely reproducible ordering. md5 turns each key into a
--     fixed-length hash string whose ordering has no relationship to geography, so sorting by it
--     scatters the chosen cells across the lattice instead of returning one contiguous corner of the
--     map. Because the hash of a given key never changes, the same cells are chosen on every run --
--     unlike random(), which would make the sample unreproducible.
--
--   LIMIT cell_limit
--     Keeps only the first cells in that hashed order. Combined with the deterministic sort, this is
--     a stable pseudo-random sample rather than a page of results.
WITH observed AS (
    SELECT
        feature.properties->>'cellKey' AS entity_key,
        substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day
    FROM geo.features AS feature
    INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
    WHERE layer.name = :layer_name
      AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
    GROUP BY 1, 2
)
SELECT observed.entity_key
FROM observed
GROUP BY observed.entity_key
HAVING count(*) >= :min_observed_days
ORDER BY md5(observed.entity_key)
LIMIT :cell_limit
