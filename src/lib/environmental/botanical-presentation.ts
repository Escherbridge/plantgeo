/**
 * Adapts `botanical-occurrences-client.ts`'s decoded answer into the vocabulary the three
 * botanical layer components and `BotanicalOccurrenceDetails` already speak.
 *
 * TWO VOCABULARIES EXIST FOR ONE PLANE, and this module is the seam between them rather than an
 * attempt to pick a winner:
 *
 *  - `src/lib/server/services/botanical-occurrences-client.ts` decodes the wire into CAMELCASE
 *    (`occurrenceId`, `documentedTaxa`), which is this codebase's convention for a decoded
 *    server type and matches every other service client.
 *  - `src/lib/botanical-occurrences.ts` declares the SNAKE_CASE wire shapes verbatim
 *    (`occurrence_id`, `documented_taxa`), and the three layer components plus the details panel
 *    were all built against those -- `botanicalOccurrencesToGeoJSON` reads `feature.occurrence_id`
 *    and every MapLibre paint filter keys on `["get", "spatial_class"]`.
 *
 * Converting HERE rather than in either of those files is deliberate. Rewriting the client would
 * edit a module that is already built and contract-tested (and whose camelCase decoding is the
 * house style); rewriting the four view files would churn their MapLibre expressions and their
 * test suite for a rename. One adapter, in the presentation layer where the other lanes keep
 * theirs (`parquet-presentation.ts`), costs a file and makes the mismatch visible instead of
 * letting `undefined` reach a paint filter -- which is what a missed field would do, silently,
 * since `["get", "occurrence_id"]` on a feature that carries `occurrenceId` simply matches
 * nothing and draws an empty layer.
 */

import type {
  BotanicalOccurrenceCell,
  BotanicalOccurrenceFeature as DecodedOccurrenceFeature,
} from "@/lib/server/services/botanical-occurrences-client";
import type {
  BotanicalAggregateCell,
  BotanicalCellEvaluation,
  BotanicalEventPrecision,
  BotanicalMembership,
  BotanicalOccurrenceFeature,
  BotanicalResolutionState,
  BotanicalSpatialClass,
} from "@/lib/botanical-occurrences";

/**
 * The wire declares these as bare `string`; the view vocabulary declares them as closed unions.
 *
 * Asserted rather than validated, and this is the honest description of what happens: the
 * service is the authority on its own enums, the client's zod schema already proved the field is
 * a string, and a value outside the union reaches a MapLibre `case` expression that falls to its
 * documented default (`BotanicalRichnessLayer`'s "transparent -- never drawn as if it were
 * data"). Narrowing with a runtime whitelist here would DROP a state the service added, which is
 * strictly worse than drawing it as unclassified: the cell would vanish rather than appear
 * uncoloured, and nothing would say why.
 */
function asDeclared<T extends string>(value: string): T {
  return value as T;
}

/** One decoded occurrence in the vocabulary the detail layer and the details panel read. */
export function presentBotanicalOccurrence(
  feature: DecodedOccurrenceFeature
): BotanicalOccurrenceFeature {
  return {
    occurrence_id: feature.occurrenceId,
    collection_key: feature.collectionKey,
    source_record_key: feature.sourceRecordKey,
    taxon_concept_id: feature.taxonConceptId,
    resolution_state: asDeclared<BotanicalResolutionState>(feature.resolutionState),
    // The view type declares these non-null where the wire allows null. An unnamed specimen is a
    // real record the plane serves, so it is captioned rather than dropped -- dropping it would
    // remove a dot from the map for a missing label.
    scientific_name: feature.scientificName ?? "Unnamed specimen record",
    family: feature.family,
    event_interval: {
      start: feature.eventInterval.start,
      end: feature.eventInterval.end,
      precision: asDeclared<BotanicalEventPrecision>(feature.eventInterval.precision ?? "unknown"),
    },
    longitude: feature.longitude,
    latitude: feature.latitude,
    coordinate_uncertainty_m: feature.coordinateUncertaintyMeters,
    spatial_class: asDeclared<BotanicalSpatialClass>(feature.spatialClass),
    // `membership: null` on the wire means the plane did not classify the determination. The
    // detail layer's three paint filters all test membership explicitly, so a null would draw
    // nothing at all; `possible` is the conservative reading -- an unclassified determination is
    // shown as uncertain rather than promoted to admitted evidence.
    membership: asDeclared<BotanicalMembership>(feature.membership ?? "possible"),
    catalog_number: feature.catalogNumber,
    recorded_by: feature.recordedBy,
    basis_of_record: feature.basisOfRecord,
    rights_uri: feature.rightsUri,
    attribution_text: feature.attributionText,
  };
}

/** One decoded support cell in the vocabulary the two aggregate layers read. */
export function presentBotanicalCell(cell: BotanicalOccurrenceCell): BotanicalAggregateCell {
  return {
    cell_id: cell.cellId,
    geometry: cell.geometry,
    evaluation: asDeclared<BotanicalCellEvaluation>(cell.evaluation),
    documented_taxa: cell.documentedTaxa,
    record_count: cell.recordCount,
    // The view type declares a number where the wire allows null. Zero is correct here rather
    // than a dropped cell: the effort layer interpolates `event_estimate` into a colour, and a
    // null would produce no colour at all for a cell the plane did return.
    event_estimate: cell.eventEstimate ?? 0,
    collection_count: cell.collectionCount,
    excluded_by_qc: cell.excludedByQc,
    possible_only_records: cell.possibleOnlyRecords,
  };
}
