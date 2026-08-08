-- Purpose: pick the one governed release set a training receipt binds its inputs to -- the
--          newest validated release that already existed at the run's as-of instant.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: as_of_time (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Why a training run needs one at all: agri.validate_forecast_training_run refuses a receipt
-- whose feature snapshot is not pinned to a validated release set whose manifest checksum
-- matches the snapshot's input_release_checksum. The release set is the answer to "which
-- governed inputs was this model allowed to see", and this statement reads it rather than
-- minting one -- a release set is a governance artifact of the ingest lane, and a training
-- lane that created its own would be certifying its own inputs.
--
-- How this query works, clause by clause:
--
--   WHERE state IN ('validated', 'published')
--     A draft release set has not been through review, so its manifest is not evidence yet.
--
--   AND validated_at IS NOT NULL AND manifest_checksum IS NOT NULL
--     Belt and braces against a row that claims a validated state without the two facts that
--     state is supposed to imply. A NULL checksum here would become a NULL input checksum on
--     the snapshot, which its own CHECK constraint rejects mid-transaction.
--
--   AND as_of_time <= (the run's as-of instant)
--     Time honesty applied to provenance, not just to values: a release set minted after the
--     knowledge cutoff this run claims to stand at cannot be the one it read its inputs
--     through. Without this predicate a hindcast pinned to 2023 would cite a 2026 release.
--
--   ORDER BY as_of_time DESC, logical_key DESC
--     Newest admissible release first. logical_key breaks a tie between two releases sharing
--     an instant, so the choice is deterministic rather than whatever order the planner
--     happened to emit -- a run that picked differently on a re-run would checksum differently.
--
--   LIMIT 1
--     Exactly one release set is bound. The caller fails loudly when this returns no row,
--     rather than proceeding with an unpinned lineage.
SELECT release.id,
       release.logical_key,
       release.manifest_checksum,
       release.as_of_time
FROM agri.release_set AS release
WHERE release.state IN ('validated', 'published')
  AND release.validated_at IS NOT NULL
  AND release.manifest_checksum IS NOT NULL
  AND release.as_of_time <= CAST(:as_of_time AS timestamptz)
ORDER BY release.as_of_time DESC, release.logical_key DESC
LIMIT 1
