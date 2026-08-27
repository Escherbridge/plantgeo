-- vegetation_publication_enqueue
-- Purpose: enqueue exact governed vegetation day fingerprints without losing prior attempts.
-- Loaded by: agri_data_service.db.vegetation_publication
-- Params: observed_days (date[]), source_fingerprints (text[]), force (boolean)
WITH target AS (
    SELECT queued.observed_day, queued.source_fingerprint
    FROM unnest(CAST(:observed_days AS date[]), CAST(:source_fingerprints AS text[]))
        AS queued(observed_day, source_fingerprint)
), upserted AS (
    INSERT INTO agri.vegetation_publication_day(
        observed_day,
        source_fingerprint,
        published_fingerprint,
        first_enqueued_at,
        last_enqueued_at,
        last_error
    )
    SELECT
        target.observed_day,
        target.source_fingerprint,
        NULL,
        clock_timestamp(),
        clock_timestamp(),
        NULL
    FROM target
    ON CONFLICT (observed_day) DO UPDATE
    SET source_fingerprint = EXCLUDED.source_fingerprint,
        published_fingerprint = CASE
            WHEN CAST(:force AS boolean)
                OR agri.vegetation_publication_day.source_fingerprint IS DISTINCT FROM EXCLUDED.source_fingerprint
            THEN NULL
            ELSE agri.vegetation_publication_day.published_fingerprint
        END,
        last_enqueued_at = CASE
            WHEN EXCLUDED.source_fingerprint IS DISTINCT FROM agri.vegetation_publication_day.source_fingerprint
                OR CAST(:force AS boolean)
            THEN clock_timestamp()
            ELSE agri.vegetation_publication_day.last_enqueued_at
        END,
        last_error = CASE
            WHEN EXCLUDED.source_fingerprint IS DISTINCT FROM agri.vegetation_publication_day.source_fingerprint
                OR CAST(:force AS boolean)
            THEN NULL
            ELSE agri.vegetation_publication_day.last_error
        END
    RETURNING observed_day
)
SELECT observed_day FROM upserted ORDER BY observed_day
