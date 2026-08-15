-- Purpose: read the pinned covariate vector for one cell over a bounded window, one row per
--          (day, feature), so the caller can pivot it into ordered vectors and report coverage.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: cell_id (uuid), window_start/window_end (timestamptz), as_of_time (timestamptz),
--         schema_version (varchar: 'agri_covariates_v1' | 'agri_covariates_v2'),
--         row_limit (int: hard cap; the caller sizes it from window days x feature count)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- agri.covariate_daily_features is the one canonical definition of the vector: it owns the
-- strict lagging, the availability gate, the per-issue-date revision pick that v2 adds, and
-- the partial-stays-partial rule (a rolling window missing an input returns NULL with
-- input_count < expected_input_count rather than a mean over the survivors). This file only
-- bounds and orders it. The ORDER BY is what makes the pivot deterministic: feature_index is
-- the pinned vector position, so a vector assembled from these rows is reproducible.
SELECT
    feature.observed_date,
    feature.feature_index,
    feature.feature_name,
    feature.feature_kind,
    feature.feature_value,
    feature.input_count,
    feature.expected_input_count,
    feature.is_imputed,
    feature.source_release_count,
    feature.data_available_at
FROM agri.covariate_daily_features(
    CAST(:cell_id AS uuid),
    CAST(:window_start AS timestamptz),
    CAST(:window_end AS timestamptz),
    CAST(:as_of_time AS timestamptz),
    CAST(:schema_version AS varchar)
) AS feature
ORDER BY feature.observed_date, feature.feature_index
LIMIT CAST(:row_limit AS integer)
