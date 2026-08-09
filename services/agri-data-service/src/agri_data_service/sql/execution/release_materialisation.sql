-- Purpose: measure what one governed source release actually holds, so a registration can be
--          checked against the corpus it claims instead of against the rows one pass happened to add.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: source_release_id (uuid) -- the release whose materialised observations are counted.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies. For the same reason the
-- clause walkthrough below names columns and parameters in prose rather than quoting the statement.
--
-- What this returns: exactly one row, always -- observation_count, series_count, first_observed_at,
-- last_observed_at. A release holding nothing still produces a row, with both counts 0 and both
-- times empty (NULL). The caller turns that into an explicit failure, because a governed release
-- that attests a corpus and holds none of it is worse than no release at all.
--
-- Why this exists as a separate read: load_observations.sql reports the rows THAT call inserted, and
-- a correct re-run inserts none because every row already exists (its ON CONFLICT DO NOTHING skips
-- them). "Inserted nothing" therefore cannot distinguish a healthy repeat from a lane that landed
-- nothing at all. This statement asks the different, decisive question -- what is attached to the
-- release now -- which is the same question either way.
--
-- How this query works, clause by clause:
--
--   count(*)::bigint AS observation_count
--     How many governed observations are attached to this release. The cast pins the width the
--     caller reads, rather than leaving it to the driver's default mapping.
--
--   count(DISTINCT observation.series_id)::bigint AS series_count
--     How many distinct cell series those observations cover. DISTINCT counts each series once
--     however many days it contributed, so this is a count of covered cells, not of rows. The two
--     counts answer different questions: a release can hold many rows over very few cells, which is
--     a partial materialisation wearing a healthy row count's clothes.
--
--   min(observation.observed_at) / max(observation.observed_at)
--     The publisher-named span the release really covers, as opposed to the span its registered
--     observed_from/observed_to claim. Reported so a reader can see the two disagree.
--
--   the source_release_id condition
--     Restricts the count to one release.
--
--     Do not assume this is cheap. Measured on production (EXPLAIN ANALYZE, 184,409-row release):
--     the planner did NOT use ix_forecast_observation_release_available; it read every row through
--     ix_forecast_observation_series_time with this condition as a filter -- 12,535 shared buffers
--     hit, 39 ms. Two reasons, both structural. count(DISTINCT series_id) and min/max(observed_at)
--     read columns that index does not carry, so an index-only plan is impossible whatever the
--     planner picks; and today this release is the whole table, so the condition selects 100 % of
--     rows and carries no selectivity to exploit. 39 ms once per registration is acceptable, and
--     that is the whole justification -- not an index. Re-measure before reusing this shape on a
--     hot path or on a table holding many releases.
--
--   (no GROUP BY)
--     A SELECT made entirely of aggregates over a filtered table collapses to exactly one row, even
--     when the filter matches nothing -- which is why the empty case arrives as a row of zeroes and
--     NULLs rather than as no row at all, and why the caller can read it without a null check on the
--     row itself.
SELECT
    count(*)::bigint AS observation_count,
    count(DISTINCT observation.series_id)::bigint AS series_count,
    min(observation.observed_at) AS first_observed_at,
    max(observation.observed_at) AS last_observed_at
FROM agri.forecast_observation AS observation
WHERE observation.source_release_id = :source_release_id
