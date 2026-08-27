-- vegetation_publication_pending
-- Purpose: fairly select durable vegetation days whose published fingerprint is behind.
-- Loaded by: agri_data_service.db.vegetation_publication
-- Params: limit (integer)
SELECT observed_day, source_fingerprint
FROM agri.vegetation_publication_day
WHERE published_fingerprint IS DISTINCT FROM source_fingerprint
ORDER BY last_attempted_at ASC NULLS FIRST, first_enqueued_at, observed_day
LIMIT CAST(:limit AS integer)
