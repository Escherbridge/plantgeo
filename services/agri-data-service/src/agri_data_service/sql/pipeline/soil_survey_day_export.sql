-- soil_survey_day_export
-- Purpose: export the current published state of one batch of USDA SSURGO delineations
--          (geo.geometry + geo.features, layer 'soil-survey') to Parquet at (mupolygonkey)
--          grain, for one release day.
-- Loaded by: agri_data_service.pipeline.lanes.soil_survey
-- Params: natural_keys (text[] -- fully-namespaced `usda-sda:<mupolygonkey>` keys for this
--         batch, built by the caller from raw SSURGO mupolygonkeys; NEVER empty, see the
--         batching note in soil_survey.py),
--         release_day (date -- the day THIS EXPORT represents, bound into every row; NOT a
--         per-row filter, see below)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS LANE IS STATIC, horizon: none (docs/lanes/soil-survey.md sections 2, 7). Like
-- watersheds_day_export.sql and unlike signal_plane_day_export.sql, there is no daily-resampled
-- axis to filter by: SSURGO issues no periodic re-observation, only a survey area's own
-- irregular republication (docs/lanes/soil-survey.md section 2). `release_day` is therefore a
-- caller-supplied constant broadcast onto every row, exactly like watersheds_day_export.sql's
-- own `release_day`, never a predicate here.
--
-- GRAIN IS mupolygonkey, EXPLICITLY NOT mukey (docs/lanes/soil-survey.md section 4): one Boise
-- viewport measured 683 delineations collapsing onto only 98 distinct mukeys
-- (src/lib/server/AGENTS.md:367-369, restated at usda-soil.ts:595-598). `mukey` rides along as
-- an informational column only, never the join key or the sort key.
--
-- ONLY THE CURRENT VINTAGE IS EXPORTABLE, NOT PRIOR CLOSED geo.geometry VERSIONS. geo.features
-- holds current state only, refreshed in place on every republish
-- (drizzle/0008_geometry_dimension.sql:94-97 comment; the UPDATE at
-- src/lib/server/services/usda-soil.ts:884-902 repoints the SAME feature row's geometry_id
-- rather than inserting a second one). A closed geo.geometry row therefore has no surviving
-- muname/drainagecl/... attributes left to export -- its shape is retained only for its
-- `superseded_by` lineage pointer, not as a second exportable delineation. This is the answer
-- this export gives to docs/lanes/soil-survey.md section 7's open design question, forced by
-- what the source data can actually still answer, not chosen as a simplification: vintage is
-- NOT discarded, though -- `version_valid_from` (this row's current vintage) rides every row
-- below as `survey_area_vintage`, so the Type-2 boundary survives as a column even though the
-- closed side of the chain does not survive as more rows.
--
-- The batch filter rides `uq_geometry_current`, the partial unique index on (natural_key)
-- WHERE version_valid_to IS NULL (drizzle/0008_geometry_dimension.sql:76); the join to
-- geo.features rides ix_features_geometry_id. Both indexed, so this scopes cheaply to
-- exactly the batch of delineations asked for, regardless of how large the full release is.
-- docs/lanes/soil-survey.md section 5, point 8 measured the PNW envelope alone at 1,507,623
-- delineations -- an unscoped "read everything" query, the shape watersheds_day_export.sql
-- uses for its ~9,396-row national HUC12 set, is not a safe assumption at this lane's scale, so
-- this query is deliberately batch-scoped instead.
SELECT
    g.natural_key::text AS natural_key,
    f.properties ->> 'id' AS mupolygonkey,
    COALESCE(f.properties ->> 'mukey', '') AS mukey,
    f.properties ->> 'muname' AS map_unit_name,
    f.properties ->> 'soilSeries' AS soil_series,
    f.properties ->> 'drainageClass' AS drainage_class,
    (f.properties ->> 'hydric')::boolean AS hydric_rating,
    f.properties ->> 'landCapabilityClass' AS land_capability_class,
    f.properties ->> 'areaSymbol' AS survey_area_symbol,
    g.version_valid_from AS survey_area_vintage,
    g.geometry_id::text AS geometry_id,
    g.last_confirmed_at AS last_confirmed_at,
    (:release_day)::date AS release_day,
    ST_AsBinary(g.geom) AS geometry_wkb,
    g.producer::text AS producer
FROM geo.geometry AS g
JOIN geo.features AS f ON f.geometry_id = g.geometry_id
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE g.natural_key = ANY(:natural_keys)
  AND g.version_valid_to IS NULL
  AND g.producer = 'usda-sda'
  AND l.name = 'soil-survey'
  AND f.status = 'published'
ORDER BY mupolygonkey
