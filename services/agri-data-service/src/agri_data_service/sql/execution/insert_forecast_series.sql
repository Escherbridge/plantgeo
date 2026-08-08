-- Purpose: register one forecast series per already-registered spatial cell in a batch, describing
--          the metric, its spatial and temporal support, and its lineage.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: series_key_prefix (text) -- the namespace every NDVI series key starts with;
--         source_variant_key (text) -- which variant of the source transform produced this series;
--         data_source_id (uuid) -- the registered Sentinel-2 NDVI source; transform_version (text) --
--         the immutable name of that transform; entity_type (text) -- what one series is about, here
--         'grid_cell'; metric_name and metric_unit (text) -- what is measured and in what units;
--         grid_name (text) -- the analysis grid, which is also the cell-key prefix; prefixed_cell_keys
--         (text[]) -- one batch of grid-qualified cell keys; metadata_json (text holding JSON) --
--         the lane facts pinned onto every series.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per series this statement actually inserted, holding its new id. The
-- caller counts those rows, so the count means "newly created", not "in existence": a series already
-- registered by an earlier pass produces no row.
--
-- What a series is: the addressable thing a forecast is about -- one metric, for one entity, at one
-- spatial and temporal resolution, from one source transform. Every observation and every forecast
-- value hangs off a series, which is why the series row records the support it was defined at rather
-- than leaving readers to infer it.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.forecast_series (...) SELECT ... FROM agri.spatial_cell AS cell
--     The rows to insert come from a query over the already-registered spatial cells rather than
--     from literal VALUES, so the whole batch is registered in one statement and every series is
--     guaranteed to point at a cell that really exists. Most of the selected values are the same
--     scalar parameter repeated on every row; only the key, the entity key and the two resolutions
--     vary per cell.
--
--   series_key_prefix || ':' || cell.cell_key
--     The double-pipe operator joins strings. The series key is built from the namespace and the
--     grid-qualified cell key, so a key is self-describing and unique across grids.
--
--   right(cell.cell_key, length(cell.cell_key) - length(grid_name) - 1)
--     Recovers the bare entity key by stripping the stored key's grid prefix and the ':' that follows
--     it. right(value, n) returns the last n characters, and the arithmetic computes exactly how many
--     characters remain after the prefix. Cutting by measured length rather than by searching for a
--     separator is what keeps this correct when the entity key itself contains a colon.
--
--   'forecast_observation' / 'aggregate' / 'native_grid_cell' / 'daily_mean'
--     Four literals rather than parameters, because they are properties of this lane and not of a
--     request: values arrive through the observation adapter; each value represents an aggregate over
--     its cell rather than a point reading; that aggregate is supported by one native grid cell; and
--     the aggregation performed is the daily mean. Recording them on the row means a consumer never
--     has to guess what a number means.
--
--   cell.resolution_m twice (source and output spatial resolution)
--     Deliberately equal: this lane does not resample. Storing both columns anyway keeps the schema
--     honest for lanes that do, and makes the absence of resampling an explicit recorded fact rather
--     than an assumption.
--
--   interval '1 day' twice (source and output temporal support)
--     An interval literal is a span of time, and here it states that one value covers one whole day
--     on both the input and the output side -- again equal because this lane does not re-bucket time.
--
--   CAST(metadata_json AS jsonb)
--     The parameter arrives as text -- the caller serialises a Python dictionary to a JSON string --
--     while the column stores a parsed JSON document. The cast performs that parse and also pins the
--     parameter's type, which the database will not guess for a bare parameter.
--
--   WHERE cell.grid_name = ... AND cell.cell_key = ANY(CAST(prefixed_cell_keys AS text[]))
--     ANY means "equals any element of this array" -- the set-membership form of an equality test,
--     which is how one statement covers a whole batch of keys. The cast pins the parameter to an
--     array of text, which the database will not infer on its own. The grid_name condition is not
--     redundant with the prefixed keys: it is a second, independent guard that a key can only ever
--     resolve to a cell on the grid it names.
--
--   ON CONFLICT DO NOTHING
--     Idempotency. Written without naming columns or a constraint, meaning "if this row would violate
--     ANY uniqueness constraint on the table, skip it silently". A series registered by an earlier
--     pass is therefore skipped rather than failing on the uniqueness constraint and aborting the
--     surrounding transaction.
--
--   RETURNING id
--     Asks the INSERT to hand back a row for each row it actually wrote. Skipped conflicts are not
--     written and so are not returned, which is what makes the caller's count mean "newly created".
INSERT INTO agri.forecast_series (
    series_key, source_variant_key, input_adapter, data_source_id,
    source_transform_version, entity_type, entity_key, metric_name, metric_unit,
    spatial_cell_id, representation_kind, spatial_support_kind,
    source_spatial_resolution_m, output_spatial_resolution_m,
    source_temporal_support, output_temporal_support, aggregation_method, metadata_json
)
SELECT
    :series_key_prefix || ':' || cell.cell_key,
    :source_variant_key,
    'forecast_observation',
    :data_source_id,
    :transform_version,
    :entity_type,
    right(cell.cell_key, length(cell.cell_key) - length(:grid_name) - 1),
    :metric_name,
    :metric_unit,
    cell.id,
    'aggregate',
    'native_grid_cell',
    cell.resolution_m,
    cell.resolution_m,
    interval '1 day',
    interval '1 day',
    'daily_mean',
    CAST(:metadata_json AS jsonb)
FROM agri.spatial_cell AS cell
WHERE cell.grid_name = :grid_name
  AND cell.cell_key = ANY(CAST(:prefixed_cell_keys AS text[]))
ON CONFLICT DO NOTHING
RETURNING id
