-- Purpose: store the fitted ridge model itself -- its coefficients and standardization
--          moments -- as one checksum-bound inline artifact.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: uri (text), checksum_sha256 (text), metadata_json (jsonb as text),
--         model_document (text: the canonical JSON serialization of the model)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Why the model is stored inline rather than as a URI to somewhere else: agri.artifact
-- carries a CHECK constraint that recomputes sha256 over content_bytes and compares it to
-- checksum_sha256, and a second one comparing octet_length(content_bytes) to size_bytes. So
-- storing the bytes here means the DATABASE re-verifies the model digest at write time. A
-- pointer to an external object could not be verified at all, and the digest is what binds
-- this model to the training receipt and to the job output that certifies it.
--
-- How this query works, clause by clause:
--
--   FROM (SELECT convert_to(CAST(... AS text), 'UTF8') AS bytes) AS document
--     A subquery in FROM used purely to name one expression so the rest of the statement can
--     refer to it more than once. convert_to() turns text into bytea by encoding it in the
--     named character set -- UTF-8 here, which is the encoding the caller hashed. Doing the
--     encoding in SQL rather than in the driver means the bytes that get hashed by the CHECK
--     constraint are provably the bytes derived from the same text the caller digested.
--
--   INSERT ... SELECT rather than INSERT ... VALUES
--     Needed because two of the inserted columns -- size_bytes and content_bytes -- are both
--     derived from that one expression. VALUES cannot reference a FROM clause; SELECT can.
--
--   octet_length(document.bytes)
--     The byte length, not the character length. A multi-byte character counts once in
--     character length and several times in byte length, and the constraint compares against
--     the byte length.
--
--   storage_class = 'database_inline'
--     Declares that the content lives in this row. A CHECK constraint requires content_bytes
--     to be non-NULL for exactly this storage class, so the declaration and the content
--     cannot drift apart.
--
--   ON CONFLICT (uri, checksum_sha256) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". The uri embeds the model digest, so an identical fit produces an identical
--     (uri, checksum) pair and is stored once rather than twice -- content addressing. When
--     the conflict path is taken nothing is inserted, so RETURNING yields no row and the
--     caller resolves the existing id with its own one-line lookup.
INSERT INTO agri.artifact (
    kind,
    uri,
    media_type,
    checksum_sha256,
    size_bytes,
    storage_class,
    metadata_json,
    content_bytes
)
SELECT 'forecast_model',
       CAST(:uri AS varchar),
       'application/json',
       CAST(:checksum_sha256 AS varchar),
       octet_length(document.bytes),
       'database_inline',
       CAST(:metadata_json AS jsonb),
       document.bytes
FROM (SELECT convert_to(CAST(:model_document AS text), 'UTF8') AS bytes) AS document
ON CONFLICT (uri, checksum_sha256) DO NOTHING
RETURNING id
