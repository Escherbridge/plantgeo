-- Purpose: register the Sentinel-2 NDVI data source row that every governed NDVI release hangs off,
--          creating it once and leaving an existing one untouched.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: key (text) -- the source's stable identifier, unique across the warehouse; name, owner,
--         purpose, base_url, license_name, license_url, citation (text) -- the human-facing
--         provenance and licence facts; refresh_policy (text holding JSON) -- how often the upstream
--         product refreshes; reviewed_at (timestamptz) and reviewed_by (text) -- who approved this
--         source and when; configuration (text holding JSON) -- the lane settings this source was
--         reviewed under.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: nothing. It either writes one data_source row or does nothing at all. The
-- caller reads the row's id back with a separate one-line lookup, which works whether this run
-- created the row or a previous one did.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.data_source (...) VALUES (...)
--     A single-row insert with the target columns listed explicitly. Listing them means the
--     statement keeps working if the table later gains a column, instead of silently shifting every
--     value one place to the left.
--
--   CAST(refresh_policy AS jsonb) and CAST(configuration AS jsonb)
--     Both parameters arrive as text -- the caller serialises a Python dictionary to a JSON string --
--     and these two columns store parsed JSON documents, not strings. The cast performs that parse,
--     and also pins the parameter's type, which the database will not guess on its own. A malformed
--     document fails here rather than being stored as unreadable text.
--
--   true, 'approved', true  (allowed_client_exposure, review_state, is_active)
--     Three values are literals rather than parameters because they are properties of this lane, not
--     of a request. Sentinel-2 NDVI is openly licensed, so it may be exposed to clients; the source
--     is registered already reviewed, with the reviewer and review time recorded in the parameters
--     beside them; and it is active from the moment it exists.
--
--   ON CONFLICT (key) DO NOTHING
--     Idempotency. key is declared unique, so a second registration pass would normally fail with a
--     uniqueness error and abort the surrounding transaction. DO NOTHING makes the duplicate a no-op
--     instead. It is deliberately DO NOTHING and not DO UPDATE: the stored row is the reviewed
--     record of what was approved, and a re-run must not quietly rewrite an approval.
INSERT INTO agri.data_source (
    key, name, owner, purpose, base_url, license_name, license_url, citation,
    refresh_policy, allowed_client_exposure, review_state, reviewed_at, reviewed_by,
    is_active, configuration
)
VALUES (
    :key, :name, :owner, :purpose, :base_url, :license_name, :license_url, :citation,
    CAST(:refresh_policy AS jsonb), true, 'approved', :reviewed_at, :reviewed_by,
    true, CAST(:configuration AS jsonb)
)
ON CONFLICT (key) DO NOTHING
