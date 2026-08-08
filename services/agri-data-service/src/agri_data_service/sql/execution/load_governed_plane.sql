-- Purpose: resolve one publisher-day release-set key back to the whole governed NDVI plane -- its
--          source, its release, its release set, and the checksums that pin them together.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: logical_key (text) -- the release set's stable key, which encodes the publisher-day
--         cutoff; data_source_key (text) -- the data source the release must belong to.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: at most one row describing the registered plane -- the ids of the data source,
-- the source release and the release set, the release's payload checksum, its observed window, its
-- quality summary, and the set's manifest checksum and state. The caller treats "no row" as an
-- explicit error ("this cutoff was never registered") and refuses to proceed unless the state is
-- validated or published, so a still-draft set cannot be used as a forecasting input. Because the
-- state is selected rather than filtered on, the caller can say WHICH wrong state it found instead
-- of only reporting that nothing matched.
--
-- How this query works, clause by clause:
--
--   FROM agri.release_set AS release_set
--     The query starts at the governed set and walks outward, because the set is what a caller can
--     name. Everything else is reached from it.
--
--   INNER JOIN agri.release_set_item AS member ON member.release_set_id = release_set.id
--     Membership of a set lives in its own table, one row per (set, release) pairing, because a set
--     can hold several releases and a release can belong to several sets. This join steps through it.
--
--   INNER JOIN agri.source_release AS release ON release.id = member.source_release_id
--   INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
--     Two more steps along the lineage: membership names a release, and a release names the source it
--     came from. Every join is INNER, so a set whose membership or lineage is incomplete yields no
--     row at all rather than a row with empty columns -- the safe direction, because the caller's
--     next act is to pin forecasts to whatever comes back.
--
--   WHERE release_set.logical_key = ... AND source.key = ...
--     Names the set, then restricts to the one data source this lane governs. The second condition is
--     what keeps the result a single row: a set that also carried releases from other sources would
--     otherwise return one row per member, and the caller reads the result as at-most-one.
--
--   release.quality_summary
--     The JSON document written at registration time, carrying the corpus statistics -- how many
--     cells, how many cell-days, how many source rows. It is read back rather than recomputed so the
--     plane reports the corpus as it was when governed, not as the underlying tables look now.
--
--   release.observed_from / release.observed_to
--     The half-open window the corpus covers: from the first observed midnight up to, but not
--     including, the midnight after the last observed day. The caller subtracts a day from the upper
--     bound to recover the last day actually observed.
SELECT
    source.id AS data_source_id,
    release.id AS source_release_id,
    release.payload_checksum,
    release.observed_from,
    release.observed_to,
    release.quality_summary,
    release_set.id AS release_set_id,
    release_set.manifest_checksum,
    release_set.state
FROM agri.release_set AS release_set
INNER JOIN agri.release_set_item AS member ON member.release_set_id = release_set.id
INNER JOIN agri.source_release AS release ON release.id = member.source_release_id
INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE release_set.logical_key = :logical_key
  AND source.key = :data_source_key
