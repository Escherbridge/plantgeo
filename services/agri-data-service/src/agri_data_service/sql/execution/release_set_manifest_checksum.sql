-- Purpose: derive the manifest checksum that names one governed NDVI release set.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: prefix (text) -- a fixed namespace string identifying this manifest scheme and its
--         version; logical_key (text) -- the release set's human-readable key, which encodes the
--         publisher-day cutoff; payload_checksum (text) -- the fingerprint of the observation corpus
--         the set is built from.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row with one unnamed column -- a 64-character lowercase hexadecimal
-- SHA-256 string. This statement reads no table at all; it is pure computation, performed in the
-- database rather than in Python so that the manifest checksum is produced by the same hashing
-- implementation that produces every other checksum in this lineage.
--
-- How this query works, clause by clause:
--
--   concat_ws('|', ..., ..., ...)
--     Joins the three inputs into one string with a '|' between them ("with separator" is what the
--     ws stands for). The separator matters: without it, two different splits of the same characters
--     would produce the same joined string and therefore the same checksum. The order of the three
--     is part of the scheme and must not be rearranged, since a different order is a different
--     checksum for the same release.
--
--   CAST(prefix AS text) and the two casts beside it
--     Casts that exist purely to pin each parameter's type. A bare bind parameter carries no type of
--     its own, and concat_ws accepts arguments of many types, so the database has nothing to resolve
--     them from and refuses the statement. Naming text settles it. They convert nothing -- all three
--     values are already text.
--
--   digest(..., 'sha256')
--     Runs the SHA-256 hash over the joined string and returns raw bytes.
--
--   encode(..., 'hex')
--     Renders those raw bytes as the familiar lowercase hexadecimal string, which is the form stored
--     and compared everywhere else in the lineage.
--
--   The prefix argument
--     A namespace. Including a fixed scheme name in the hashed material means a checksum from this
--     manifest scheme can never collide with one computed by a different scheme over coincidentally
--     equal inputs, and that changing the scheme later changes every checksum it produces rather
--     than quietly reusing old identities.
SELECT encode(
    digest(
        concat_ws(
            '|',
            CAST(:prefix AS text),
            CAST(:logical_key AS text),
            CAST(:payload_checksum AS text)
        ),
        'sha256'
    ),
    'hex'
)
