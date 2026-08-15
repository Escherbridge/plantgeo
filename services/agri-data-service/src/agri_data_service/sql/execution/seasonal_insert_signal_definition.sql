-- Purpose: register one reviewed derived-signal definition/version for the evaluation-only lineage
--          plane. Idempotent on (signal_key, signal_version) so a re-run reuses the identity rather
--          than minting a second one.
-- Loaded by: agri_data_service.execution.seasonal_lineage_persist
-- Params: signal_key/signal_version/unit/spatial_support_key (text), temporal_grain (interval),
--         recipe_key (text), recipe_checksum/definition_checksum (hex64 text),
--         parent_schema (jsonb), max_dependency_depth (int)
INSERT INTO agri.forecast_signal_definition (
    signal_key,
    signal_version,
    unit,
    spatial_support_key,
    temporal_grain,
    recipe_key,
    recipe_checksum,
    parent_schema,
    max_dependency_depth,
    definition_checksum
) VALUES (
    :signal_key,
    :signal_version,
    :unit,
    :spatial_support_key,
    CAST(:temporal_grain AS interval),
    :recipe_key,
    :recipe_checksum,
    CAST(:parent_schema AS jsonb),
    :max_dependency_depth,
    :definition_checksum
)
ON CONFLICT (signal_key, signal_version) DO NOTHING
RETURNING id
