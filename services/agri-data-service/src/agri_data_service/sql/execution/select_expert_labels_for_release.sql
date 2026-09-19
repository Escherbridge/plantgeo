-- Purpose: read one reviewed expert-label release, with the two natural keys that resolve its
--          foreign keys, for the one-time Parquet export to the ML service.
-- Loaded by: agri_data_service.execution.expert_label_export
-- Params: release_key (text), limit (integer)
--
-- WHAT THIS RETURNS, in one sentence: every label belonging to the release whose business key is
-- `release_key`, in a stable order, with the release's and the source's own human-readable keys
-- carried on each row so the file needs neither of the other two tables to be understood.
--
-- Clause by clause, for a reader who has not written SQL before:
--
--   FROM agri.expert_label AS label
--     The table the rows come from. `AS label` gives it a short nickname so the rest of the
--     statement can say `label.subject` instead of repeating the whole name.
--
--   JOIN agri.expert_label_release AS release ON release.id = label.release_id
--     A JOIN glues a second table onto the first, matching rows by the condition after ON. Each
--     label stores `release_id`, which is a machine identifier (a UUID) for the release it belongs
--     to; the release table is where that identifier's human-readable `release_key` lives. A plain
--     JOIN (rather than a LEFT JOIN) means a label whose release is missing would be DROPPED. That
--     is correct here and is not a silent loss: `expert_label.release_id` is declared NOT NULL with
--     a foreign key to this table, so the database cannot hold such a row in the first place.
--
--   JOIN agri.expert_label_source AS source ON source.id = label.source_id
--     The same move for the citation the label was read out of, carrying its `source_key`.
--
--   WHERE release.release_key = CAST(:release_key AS text)
--     Keeps only the rows belonging to the one release the caller named. The CAST exists to settle
--     the parameter's type: without it the database has to guess what `:release_key` is, and a
--     guess that lands on the wrong type can turn an index lookup into a full scan.
--
--   ORDER BY label.label_key
--     Fixes the row order. `label_key` is unique across the whole plane, so this ordering leaves no
--     ties, which is what makes two exports of the same release produce byte-identical files. An
--     ordering with ties would reorder rows between runs and the export could never prove it had
--     written the same content twice.
--
--   LIMIT CAST(:limit AS integer)
--     A bound, not a page. The caller passes one MORE than the ceiling it will accept, so a release
--     that has outgrown the ceiling comes back one row over and is refused by name instead of being
--     quietly truncated to the first N labels.
SELECT
    label.label_key,
    release.release_key,
    source.source_key,
    label.label_kind,
    label.subject,
    label.subject_normalized,
    label.outcome,
    label.condition_envelope,
    label.envelope_checksum,
    label.rationale,
    label.supporting_quote,
    label.confidence,
    label.confidence_weight,
    label.harvest_slice,
    label.citation_check_refuted,
    label.citation_check_doi_resolves,
    label.citation_check_reason,
    label.review_state,
    label.review_note,
    label.reviewed_by,
    label.reviewed_at,
    label.owner_signature_reference,
    label.label_checksum,
    label.created_at
FROM agri.expert_label AS label
JOIN agri.expert_label_release AS release ON release.id = label.release_id
JOIN agri.expert_label_source AS source ON source.id = label.source_id
WHERE release.release_key = CAST(:release_key AS text)
ORDER BY label.label_key
LIMIT CAST(:limit AS integer)
