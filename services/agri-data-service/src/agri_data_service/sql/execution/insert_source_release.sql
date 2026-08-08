-- Purpose: register the immutable source release that pins one NDVI observation corpus, creating it
--          once and leaving an existing one untouched.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: data_source_id (uuid) -- the registered Sentinel-2 NDVI source; source_version (text) --
--         the upstream product version label; retrieved_at and data_available_at (timestamptz) --
--         when this service read the corpus and when the data became available to it; observed_from
--         and observed_to (timestamptz) -- the half-open time span the corpus covers; payload_checksum
--         (text) -- the corpus fingerprint; schema_version (text) -- the shape of the feature
--         properties read; license_snapshot (text) -- the licence in force at registration time;
--         query_parameters (text holding JSON) -- exactly what was asked for; quality_summary (text
--         holding JSON) -- the measured corpus statistics; validated_at (timestamptz) -- when the
--         release was judged valid; transform_version (text) -- the immutable name of the transform
--         that produced the corpus.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: nothing. It either writes one source_release row or does nothing at all. The
-- caller reads the row's id back with a companion lookup (select_source_release.sql), which works
-- whether this run created the row or an earlier one did.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.source_release (...) VALUES (...)
--     A single-row insert naming its target columns explicitly, so the statement stays correct if
--     the table later gains a column rather than shifting every value one place along.
--
--   CAST(query_parameters AS jsonb) and CAST(quality_summary AS jsonb)
--     Both parameters arrive as text -- the caller serialises a Python dictionary to a JSON string --
--     while both columns store parsed JSON documents. The cast performs that parse and also pins the
--     parameter's type, which the database will not guess for a bare parameter. A malformed document
--     fails here instead of being stored as unreadable text.
--
--   'valid'  (the validation_state column)
--     A literal rather than a parameter, because this lane only ever registers a corpus it has
--     already digested end to end. There is no path here that records a release as unvalidated.
--
--   ON CONFLICT (data_source_id, source_version, payload_checksum, transform_version) DO NOTHING
--     Idempotency, keyed on the four columns that together define a release's identity: which
--     source, which upstream version, which exact corpus contents, and which transform produced it.
--     Re-running registration against unchanged data therefore matches an existing row and does
--     nothing, instead of failing on the uniqueness constraint and aborting the transaction. It is
--     DO NOTHING rather than DO UPDATE on purpose: a source release is an immutable record of what
--     was observed, and any real change of content produces a different checksum and therefore a
--     different row, never a rewrite of this one.
INSERT INTO agri.source_release (
    data_source_id, source_version, retrieved_at, data_available_at,
    observed_from, observed_to, payload_checksum, schema_version, license_snapshot,
    query_parameters, quality_summary, validation_state, validated_at, transform_version
)
VALUES (
    :data_source_id, :source_version, :retrieved_at, :data_available_at,
    :observed_from, :observed_to, :payload_checksum, :schema_version, :license_snapshot,
    CAST(:query_parameters AS jsonb), CAST(:quality_summary AS jsonb),
    'valid', :validated_at, :transform_version
)
ON CONFLICT (data_source_id, source_version, payload_checksum, transform_version) DO NOTHING
