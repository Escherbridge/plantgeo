-- Purpose: read the provenance receipt for one cell's covariate window -- completeness
--          tallies, declared gaps, contributing source releases and the manifest checksum.
-- Loaded by: agri_data_service.execution.covariate_wind_model
-- Params: cell_id (uuid), window_start (timestamptz), window_end (timestamptz),
--         as_of_time (timestamptz), schema_version (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- manifest_checksum is the value that BINDS THE FEATURE LINEAGE of a training receipt. It is
-- a SHA-256 the database computes over the schema version, the cell, the exact window, the
-- as-of instant, the feature names in order, every completeness tally and the contributing
-- source release ids -- so two runs agree on it only if they genuinely read the same feature
-- vectors from the same governed inputs at the same knowledge cutoff. The training run and
-- its feature snapshot both store it, which is what makes "which features trained this model"
-- an answerable question rather than a claim.
--
-- How this query works, clause by clause:
--
--   SELECT * FROM agri.covariate_vector_manifest(...)
--     A set-returning function used in FROM exactly where a table name would go. It returns a
--     single row of seventeen columns; SELECT * is appropriate here precisely because the
--     caller wants the whole receipt and the function's return type is the contract. The
--     function is declarative schema (db/agri/functions/covariate_vector_manifest.sql).
--
--   CAST(... AS uuid) / CAST(... AS timestamptz) / CAST(... AS varchar)
--     The driver sends parameters untyped, and PostgreSQL cannot infer a function argument's
--     type from an untyped placeholder -- it would refuse the call with "could not determine
--     data type of parameter". Naming each type is what makes the call resolvable.
SELECT *
FROM agri.covariate_vector_manifest(
    CAST(:cell_id AS uuid),
    CAST(:window_start AS timestamptz),
    CAST(:window_end AS timestamptz),
    CAST(:as_of_time AS timestamptz),
    CAST(:schema_version AS varchar)
)
