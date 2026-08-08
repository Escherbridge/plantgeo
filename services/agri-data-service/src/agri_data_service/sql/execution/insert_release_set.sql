-- Purpose: open the draft release set for one publisher-day cutoff, creating it once and leaving an
--          existing one untouched.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: logical_key (text) -- the release set's stable, human-readable key, which encodes the
--         cutoff day; as_of_time (timestamptz) -- the moment the set claims to describe;
--         manifest_checksum (text) -- the derived fingerprint that names this set's contents;
--         description (text) -- a plain-language note about what the set covers; created_at
--         (timestamptz) -- when the row was written.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: nothing. It either writes one release_set row or does nothing at all. The
-- caller then reads the row back with a one-line lookup on logical_key, which is how it learns both
-- the id and -- crucially -- the manifest checksum and state that were stored FIRST. On a re-run
-- those stored values win over the ones this statement offered, so an existing set is never
-- redescribed by a later pass.
--
-- What a release set is: the unit of governance. A source release says "this exact corpus existed";
-- a release set says "these releases together are the approved input, and their contents are frozen
-- as of this moment". Forecasts are pinned to a release set, never to a raw table, which is what
-- makes a forecast reproducible after the underlying tables have moved on.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.release_set (...) VALUES (...)
--     A single-row insert naming its target columns explicitly, so the statement stays correct if
--     the table later gains a column instead of shifting every value one place along.
--
--   'draft'  (the state column)
--     A literal, because a set is always born mutable. Membership is added afterwards, and only once
--     the membership is complete does a separate statement move the set to 'validated'. Inserting it
--     as already validated would declare the set final before anything was in it.
--
--   ON CONFLICT (logical_key) DO NOTHING
--     Idempotency. logical_key is unique, so a second registration pass for the same cutoff day
--     would otherwise fail on the uniqueness constraint and abort the surrounding transaction. DO
--     NOTHING makes the duplicate a no-op. DO NOTHING rather than DO UPDATE is the load-bearing
--     choice: a release set that has already been validated is frozen evidence, and an update would
--     let a later run silently rewrite the manifest checksum that earlier forecasts were pinned to.
INSERT INTO agri.release_set (
    logical_key, as_of_time, manifest_checksum, state, description, created_at
)
VALUES (:logical_key, :as_of_time, :manifest_checksum, 'draft', :description, :created_at)
ON CONFLICT (logical_key) DO NOTHING
