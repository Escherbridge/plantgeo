/**
 * Which botanical collections are serving ahead of formal admission, and the label the map must
 * show for them.
 *
 * `conductor/tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json` is
 * the authority: `admitted_releases` is the admitted set and `serving_but_not_admitted` is
 * everything else that is nonetheless live in production.
 *
 * UPDATED 2026-09-19: UBC v16.43 (`pnw:UBC:vascular`) moved OUT of `serving_but_not_admitted`
 * (now empty) and INTO `admitted_releases`, by explicit owner decision -- see that track's
 * `evidence/owner-admission-decision-20260919.md`. It is nonetheless deliberately KEPT in the
 * provisional set below. The owner admitted it *over* two still-open verifications rather than
 * after them: field-map reconciliation against the raw `occurrence.txt`, and the
 * v16.42-vs-v16.43 native-ID stability comparison. Those two are exactly what
 * `PROVISIONAL_BOTANICAL_NOTICE` names, so the notice is still true of this data even though the
 * governance verdict changed. Dropping a user-facing consent/quality caveat is its own owner call;
 * removing `pnw:UBC:vascular` from this set is an open follow-up, not an automatic consequence of
 * admission. Keeping a stale-but-stricter label is the safe direction of error here.
 *
 * `gbif:pnw:vascular` was added 2026-09-14 for the same reason at admission time zero: a brand
 * new source starts provisional by construction and only leaves this set once a human evidence
 * trail (see the GBIF sibling of this track, mirroring `pnw_herbaria_source_admission_20260911/`)
 * and the ledger both say so. If Lane 1's plan lands a different collection_key string, that
 * string -- not this one -- is what belongs here; this entry is provisional in the same sense the
 * data it labels is.
 *

 * This list is NOT read from the ledger at runtime -- the ledger lives in `conductor/`, which does
 * not ship to the browser or the agri-data-service, and the plane itself carries no "admitted"
 * concept (admission is a governance decision about the COLLECTION, not a field on a record). A
 * collection moves out of this set only when a human updates both files: this one, and the ledger
 * that is its source of truth. Keeping it a tiny, hand-maintained set is deliberate -- a
 * mis-labelled provisional collection is worse than a manual step someone has to remember.
 */
/**
 * GBIF's collection_key, exported so the map layer split (`GbifOccurrencesLayer.tsx` vs.
 * `BotanicalOccurrencesLayer.tsx`) and this governance list read the SAME string rather than two
 * copies that could drift. If Lane 1's plan lands a different value, update it here only.
 */
export const GBIF_COLLECTION_KEY = "gbif:pnw:vascular";

export const PROVISIONAL_BOTANICAL_COLLECTION_KEYS: ReadonlySet<string> = new Set([
  "pnw:UBC:vascular",
  GBIF_COLLECTION_KEY,
]);

/** Whether a record's collection is serving ahead of formal governance admission. */
export function isProvisionalBotanicalCollection(collectionKey: string): boolean {
  return PROVISIONAL_BOTANICAL_COLLECTION_KEYS.has(collectionKey);
}

/**
 * The one line every botanical surface must show for a provisional-collection record, so the
 * distinction is never left to a reader noticing a collection code. Deliberately blunt: this is a
 * legal/consent posture, not a data-quality caption.
 */
export const PROVISIONAL_BOTANICAL_NOTICE =
  "Provisional: this collection is serving ahead of formal governance admission (field-map reconciliation and release-identity comparison still open).";
