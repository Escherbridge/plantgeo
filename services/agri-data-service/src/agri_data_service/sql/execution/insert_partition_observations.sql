-- insert_partition_observations
-- Purpose: materialise ONE Parquet day partition's cell values as governed NDVI observations,
--          without reading any source table.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: entity_keys (text[]), metric_values (double precision[]), observation_checksums (text[])
--           -- three equal-length arrays, zipped row by row, never independent filters;
--         observed_day (date) -- the publisher-named day the whole partition belongs to;
--         data_available_at (timestamptz) -- when this partition reached the governed plane;
--         source_release_id (uuid), grid_name (text), metric_name (varchar),
--         transform_version (varchar), metadata_json (jsonb as text).
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- This replaces load_observations_for_days.sql, which read the same rows out of geo.features. That
-- table was frozen when the postgres-vegetation lane was retired (owner call 2026-09-04), so the
-- statement it fed could only ever insert nothing. The values now arrive as parameters from the
-- partition the availability index authorised, and the day is a single scalar because one partition
-- is one day by construction.
--
-- How this query works, clause by clause:
--
--   WITH incoming AS (SELECT ... FROM unnest(CAST(...), CAST(...), CAST(...)) AS incoming(...))
--     unnest with SEVERAL arrays walks them in lockstep and hands back one row per position: the
--     first key with the first value and the first checksum, and so on. That is why the three
--     arrays must be equal-length and identically ordered -- they are columns of one table sliced
--     apart for transport, not three separate sets to be matched up by the database.
--
--   INSERT INTO agri.forecast_observation (...) SELECT ...
--     An INSERT fed by a SELECT rather than by literal rows: the whole batch lands in one statement.
--
--   CAST(:observed_day AS date)::timestamptz
--     The stored instants are the day's midnight and the next day's midnight, which is the half-open
--     window [day, day+1) this lane's observations occupy. The session pins UTC before this runs
--     (the determinism SET LOCALs in the calling module), so the cast cannot drift with a server
--     time zone.
--
--   concat_ws(':', incoming.entity_key, CAST(:observed_day AS date)::text)
--     The natural key of one observation, kept in the RAW cell-key form the retired loader used, so
--     a row written before the source cut and a row written after it name the same event the same
--     way. concat_ws joins with a separator ("with separator" is what the ws means).
--
--   INNER JOIN agri.spatial_cell ... INNER JOIN agri.forecast_series ...
--     Resolves each incoming key to the lattice cell and then to this lane's single series for that
--     cell (one metric, one transform version). An INNER JOIN drops a row whose cell or series is
--     missing; the caller has already proved every cell is registered
--     (select_unregistered_spatial_cells.sql) and has just registered the series, so a drop here
--     would be a real defect and is caught by the selection-scoped materialisation gate that runs
--     immediately after.
--
--   ON CONFLICT DO NOTHING ... RETURNING id
--     Re-running an identical promotion must not fail and must not duplicate: a row already present
--     is skipped. RETURNING then yields ONLY the rows actually inserted, which is why a healthy
--     repeat legitimately returns zero and why the caller measures materialisation separately
--     instead of trusting this count.
WITH incoming AS (
    SELECT
        incoming.entity_key,
        incoming.metric_value,
        incoming.observation_checksum
    FROM unnest(
        CAST(:entity_keys AS text[]),
        CAST(:metric_values AS double precision[]),
        CAST(:observation_checksums AS text[])
    ) AS incoming(entity_key, metric_value, observation_checksum)
)
INSERT INTO agri.forecast_observation (
    series_id, source_release_id, observed_at, valid_from, valid_to,
    data_available_at, metric_value, quality_flag, source_event_key,
    observation_checksum, metadata_json
)
SELECT
    series.id,
    :source_release_id,
    CAST(:observed_day AS date)::timestamptz,
    CAST(:observed_day AS date)::timestamptz,
    (CAST(:observed_day AS date) + 1)::timestamptz,
    :data_available_at,
    incoming.metric_value,
    'accepted',
    concat_ws(':', incoming.entity_key, CAST(:observed_day AS date)::text),
    incoming.observation_checksum,
    CAST(:metadata_json AS jsonb)
FROM incoming
INNER JOIN agri.spatial_cell AS cell
    ON cell.cell_key = CAST(:grid_name AS text) || ':' || incoming.entity_key
INNER JOIN agri.forecast_series AS series
    ON series.spatial_cell_id = cell.id
   AND series.metric_name = CAST(:metric_name AS varchar)
   AND series.source_transform_version = CAST(:transform_version AS varchar)
ON CONFLICT DO NOTHING
RETURNING id
