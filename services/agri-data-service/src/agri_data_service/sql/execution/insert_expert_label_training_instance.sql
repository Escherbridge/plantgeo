-- Purpose: persist one (label, cell, day) training instance -- the label's condition envelope
--          evaluated against the governed streams for that day -- including the envelope terms
--          the streams cannot express, as an explicit gap rather than a silent drop.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: label_id (uuid), release_id (uuid), spatial_cell_id (uuid), observed_date (date),
--         as_of_time (timestamptz), feature_schema_version (text),
--         feature_values (jsonb as text: ordered array of {feature_index, feature_name, feature_value}),
--         feature_checksum (text), envelope_match (jsonb as text: per-term verdicts),
--         unexpressible_terms (jsonb as text: array of envelope keys with no governed stream),
--         match_state (text: 'matched'|'excluded'|'unexpressible'), instance_checksum (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Excluded and unexpressible instances are stored, not discarded. The count of days a label's
-- envelope excluded is the evidence that the envelope was actually evaluated against the
-- streams, and the unexpressible terms are the data-completion gap this plane exists to make
-- visible. Only 'matched' rows are read back for a fit.
INSERT INTO agri.expert_label_training_instance (
    label_id,
    release_id,
    spatial_cell_id,
    observed_date,
    as_of_time,
    feature_schema_version,
    feature_values,
    feature_checksum,
    envelope_match,
    unexpressible_terms,
    match_state,
    instance_checksum
)
VALUES (
    CAST(:label_id AS uuid),
    CAST(:release_id AS uuid),
    CAST(:spatial_cell_id AS uuid),
    CAST(:observed_date AS date),
    CAST(:as_of_time AS timestamptz),
    CAST(:feature_schema_version AS varchar),
    CAST(:feature_values AS jsonb),
    CAST(:feature_checksum AS varchar),
    CAST(:envelope_match AS jsonb),
    CAST(:unexpressible_terms AS jsonb),
    CAST(:match_state AS varchar),
    CAST(:instance_checksum AS varchar)
)
ON CONFLICT (label_id, spatial_cell_id, observed_date, feature_schema_version) DO NOTHING
RETURNING id
