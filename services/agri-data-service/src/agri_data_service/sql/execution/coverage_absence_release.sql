-- coverage_absence_release
-- Purpose: register the one source release that carries a gap probe's evidence, and hand its id
--          back, so the governed absences written from that probe have provenance to hang on.
-- Loaded by: agri_data_service.execution.coverage_fill
-- Params: source_key (text) -- the agri.data_source.key whose lane was probed; source_version
--         (text) -- the probe's own version label; retrieved_at (timestamptz) -- when the probe
--         ran; observed_from and observed_to (timestamptz) -- the gap span that was probed;
--         payload_checksum (text) -- the fingerprint of the probe request, so re-probing the same
--         span is the same release; schema_version (text) and transform_version (text) -- the
--         probe's contract identity; query_parameters (text holding JSON) -- exactly what was
--         asked of the provider; quality_summary (text holding JSON) -- what the provider answered.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row holding source_release_id, whether this call created the
-- release or an earlier identical probe already had. It returns no row at all when no data_source
-- carries the given key, and the caller treats that emptiness as a refusal rather than inventing a
-- source -- an absence attributed to a lane that does not exist would be unfalsifiable.
--
-- Why a probe gets a release at all: agri.source_release records a FETCH, not a payload. A fetch
-- that came back empty is still a fetch, and the lanes already do exactly this -- an Open-Meteo
-- chunk whose series were entirely null mints a release and writes no_data audit rows against it.
-- Without a release row the audit table's foreign key has nothing to point at, and the absence
-- could not be recorded at all.
--
-- data_available_at is set to the same instant as retrieved_at, which for observation rows would
-- be a leakage bug and here is the literal truth: the fact being recorded is "at this moment the
-- provider had nothing", and that fact became knowable at the moment we asked. No observation row
-- is ever written against this release, so no model can learn a publication lag from it.
--
-- How this query works, clause by clause:
--
--   insert ... select ... from agri.data_source where key = source_key parameter
--     The source id and its licence text are read from the registry rather than passed in, so a
--     caller cannot attribute a probe to one lane while stamping another lane's licence on it.
--     Selecting instead of VALUES is also what makes an unknown key return no row rather than
--     failing a foreign key deep inside the statement.
--
--   'valid' (the validation_state column)
--     A literal. The probe validates itself: it either parsed the provider's answer or it raised
--     before reaching this statement, so there is no path here that registers an unchecked release.
--
--   cast(query_parameters as jsonb) and cast(quality_summary as jsonb)
--     Both arrive as text because the caller serialises a Python dictionary to a JSON string,
--     while both columns store parsed documents. The cast performs that parse and pins the
--     parameter's type, which the database will not guess for a bare parameter; a malformed
--     document fails here rather than being stored as unreadable text.
--
--   on conflict (data_source_id, source_version, payload_checksum, transform_version)
--     The four columns that together define a release's identity. Because payload_checksum is the
--     fingerprint of the probe REQUEST, probing the same span twice matches the same row, and a
--     second --apply run adds no second lineage over the same evidence.
--
--   do update set source_version = excluded.source_version
--     A deliberate no-op write. The column being assigned is part of the conflict key, so the row
--     cannot change value; the assignment exists only because ON CONFLICT DO NOTHING returns no
--     row, and the caller needs the existing id in both cases. Immutability is preserved -- nothing
--     about the release is rewritten -- while RETURNING becomes total.
insert into agri.source_release (
    data_source_id, source_version, retrieved_at, data_available_at,
    observed_from, observed_to, payload_checksum, schema_version, license_snapshot,
    query_parameters, quality_summary, validation_state, validated_at, transform_version
)
select
    source.id, :source_version, :retrieved_at, :retrieved_at,
    :observed_from, :observed_to, :payload_checksum, :schema_version, source.license_name,
    cast(:query_parameters as jsonb), cast(:quality_summary as jsonb),
    'valid', :retrieved_at, :transform_version
from agri.data_source as source
where source.key = :source_key
on conflict (data_source_id, source_version, payload_checksum, transform_version)
do update set source_version = excluded.source_version
returning agri.source_release.id as source_release_id
