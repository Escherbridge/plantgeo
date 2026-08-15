-- Purpose: move a release's draft labels to the review state their citation check earned --
--          `agent_reviewed` when the adversarial verifier did not refute them, `rejected`
--          when it did -- and never further.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: release_id (uuid), reviewed_by (text: the agent workflow identity),
--         reviewed_at (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- What this statement deliberately cannot do: reach 'approved'. That state requires an
-- owner_signature_reference, which a CHECK constraint and agri.guard_expert_label_review_change
-- both demand and which no column here supplies. An agent review is evidence for the owner to
-- countersign, not the countersignature.
--
-- WHERE review_state = 'draft' makes the statement idempotent: a re-run touches no already
-- reviewed row, so the guard trigger's immutable-after-review rule is never provoked.
UPDATE agri.expert_label AS label
   SET review_state = CASE WHEN label.citation_check_refuted THEN 'rejected' ELSE 'agent_reviewed' END,
       review_note = CASE
           WHEN label.citation_check_refuted
               THEN 'Refuted by adversarial citation verification; never trainable.'
           ELSE 'Citation verified against the cited source by an adversarial verifier; trainable as evidence pending the owner signature.'
       END,
       reviewed_by = CAST(:reviewed_by AS varchar),
       reviewed_at = CAST(:reviewed_at AS timestamptz)
 WHERE label.release_id = CAST(:release_id AS uuid)
   AND label.review_state = 'draft'
RETURNING label.id, label.review_state
