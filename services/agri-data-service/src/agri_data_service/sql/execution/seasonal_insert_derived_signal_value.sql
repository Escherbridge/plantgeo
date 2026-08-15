-- Purpose: append one immutable derived-signal value. The checksum is computed by the database's own
--          pinned function rather than supplied, so the stored digest is reproducible by definition
--          and the table's CHECK cannot be satisfied by a hand-written digest.
-- Loaded by: agri_data_service.execution.seasonal_lineage_persist
-- Params: signal_definition_id (uuid), max_dependency_depth/lineage_depth (int), series_key (text),
--         origin_cutoff_time/valid_time/availability_time (timestamptz),
--         signal_value (float8, nullable), known_missing_inputs (jsonb),
--         input_release_checksum/recipe_checksum (hex64 text)
INSERT INTO agri.forecast_derived_signal_value (
    signal_definition_id,
    max_dependency_depth,
    series_key,
    lineage_depth,
    origin_cutoff_time,
    valid_time,
    availability_time,
    signal_value,
    known_missing_inputs,
    input_release_checksum,
    recipe_checksum,
    value_checksum
) VALUES (
    CAST(:signal_definition_id AS uuid),
    :max_dependency_depth,
    :series_key,
    :lineage_depth,
    :origin_cutoff_time,
    :valid_time,
    :availability_time,
    :signal_value,
    CAST(:known_missing_inputs AS jsonb),
    :input_release_checksum,
    :recipe_checksum,
    agri.forecast_derived_signal_value_checksum(
        CAST(:signal_definition_id AS uuid),
        :series_key,
        :origin_cutoff_time,
        :valid_time,
        :availability_time,
        :lineage_depth,
        :signal_value,
        :input_release_checksum,
        :recipe_checksum
    )
)
RETURNING id, value_checksum
