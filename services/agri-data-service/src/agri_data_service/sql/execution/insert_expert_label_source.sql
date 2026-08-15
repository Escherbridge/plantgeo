-- Purpose: register the published work a harvested label cites, once per work/edition/DOI.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: source_key (text: deterministic identity, DOI-derived where a DOI exists),
--         doi (text, nullable), source_url (text, nullable), title (text),
--         publication_year (int), journal_or_publisher (text),
--         edition_or_version (text, nullable), license_posture (text),
--         source_checksum (text: sha256 over the canonical identity payload)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- ON CONFLICT (source_key) DO NOTHING: several labels legitimately cite one work, and a
-- re-run of the same harvest re-derives the same key. Nothing is inserted on the conflict
-- path, so RETURNING yields no row and the caller resolves the existing id with a one-line
-- lookup -- the same shape covariate_wind_persist uses for every identity-keyed insert.
INSERT INTO agri.expert_label_source (
    source_key,
    doi,
    source_url,
    title,
    publication_year,
    journal_or_publisher,
    edition_or_version,
    license_posture,
    source_checksum
)
VALUES (
    CAST(:source_key AS varchar),
    CAST(:doi AS varchar),
    CAST(:source_url AS varchar),
    CAST(:title AS varchar),
    CAST(:publication_year AS integer),
    CAST(:journal_or_publisher AS varchar),
    CAST(:edition_or_version AS varchar),
    CAST(:license_posture AS varchar),
    CAST(:source_checksum AS varchar)
)
ON CONFLICT (source_key) DO NOTHING
RETURNING id
