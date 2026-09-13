import { describe, expect, it } from "vitest";
import {
  presentBotanicalCell,
  presentBotanicalOccurrence,
} from "@/lib/environmental/botanical-presentation";
import type {
  BotanicalOccurrenceCell,
  BotanicalOccurrenceFeature,
} from "@/lib/server/services/botanical-occurrences-client";

/**
 * The adapter between the client's camelCase decoding and the snake_case vocabulary the layer
 * components' MapLibre paint filters key on. A missed field here does not throw: `["get",
 * "spatial_class"]` on a feature carrying `spatialClass` matches nothing and draws an empty
 * layer, which is exactly the silent failure these cases exist to catch.
 */

function decodedFeature(
  overrides: Partial<BotanicalOccurrenceFeature> = {}
): BotanicalOccurrenceFeature {
  return {
    occurrenceId: "urn:occ:1",
    collectionKey: "UBC",
    sourceRecordKey: "src-1",
    taxonConceptId: "tc-1",
    resolutionState: "resolved",
    scientificName: "Abies lasiocarpa",
    family: "Pinaceae",
    eventInterval: { start: "1987-04-01", end: "1987-06-30", precision: "interval" },
    longitude: -122.5,
    latitude: 45.9,
    coordinateUncertaintyMeters: 250,
    spatialClass: "exact",
    membership: "confirmed",
    catalogNumber: "V12345",
    recordedBy: "A. Collector",
    basisOfRecord: "PreservedSpecimen",
    rightsUri: "https://creativecommons.org/licenses/by/4.0/",
    attributionText: "UBC Herbarium",
    ...overrides,
  };
}

function decodedCell(overrides: Partial<BotanicalOccurrenceCell> = {}): BotanicalOccurrenceCell {
  return {
    cellId: "cell-1",
    geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] },
    evaluation: "documented",
    documentedTaxa: 12,
    recordCount: 40,
    eventEstimate: 30,
    collectionCount: 3,
    excludedByQc: 2,
    possibleOnlyRecords: 1,
    ...overrides,
  };
}

describe("presentBotanicalOccurrence", () => {
  it("renames every field the paint filters and the details panel read", () => {
    const presented = presentBotanicalOccurrence(decodedFeature());

    // The three the paint filters key on. A miss here is an invisible layer, not an error.
    expect(presented.occurrence_id).toBe("urn:occ:1");
    expect(presented.spatial_class).toBe("exact");
    expect(presented.membership).toBe("confirmed");
    // The provenance the hover tooltip and the details panel state.
    expect(presented.collection_key).toBe("UBC");
    expect(presented.rights_uri).toBe("https://creativecommons.org/licenses/by/4.0/");
    expect(presented.attribution_text).toBe("UBC Herbarium");
    expect(presented.coordinate_uncertainty_m).toBe(250);
    expect(presented.event_interval).toEqual({
      start: "1987-04-01",
      end: "1987-06-30",
      precision: "interval",
    });
  });

  /** An unnamed specimen is a real record; dropping it would remove a dot for a missing label. */
  it("captions an unnamed specimen rather than leaving the name null", () => {
    const presented = presentBotanicalOccurrence(decodedFeature({ scientificName: null }));
    expect(presented.scientific_name).toBe("Unnamed specimen record");
  });

  /**
   * A null membership would match none of the three paint filters and draw nothing. `possible`
   * is the conservative reading: an unclassified determination is shown as uncertain rather than
   * promoted to admitted evidence.
   */
  it("reads an unclassified determination as possible, not as confirmed", () => {
    const presented = presentBotanicalOccurrence(decodedFeature({ membership: null }));
    expect(presented.membership).toBe("possible");
  });

  it("defaults an unreported event precision rather than emitting null", () => {
    const presented = presentBotanicalOccurrence(
      decodedFeature({ eventInterval: { start: null, end: null, precision: null } })
    );
    expect(presented.event_interval.precision).toBe("unknown");
  });

  /** Genuinely absent optional fields stay absent; the adapter must not invent values. */
  it("carries a null family and a null catalog number through unchanged", () => {
    const presented = presentBotanicalOccurrence(
      decodedFeature({ family: null, catalogNumber: null, rightsUri: null })
    );
    expect(presented.family).toBeNull();
    expect(presented.catalog_number).toBeNull();
    expect(presented.rights_uri).toBeNull();
  });
});

describe("presentBotanicalCell", () => {
  it("renames every field the two aggregate layers paint from", () => {
    const presented = presentBotanicalCell(decodedCell());

    expect(presented.cell_id).toBe("cell-1");
    expect(presented.evaluation).toBe("documented");
    // The richness ramp interpolates on this one.
    expect(presented.documented_taxa).toBe(12);
    // The effort ramp interpolates on these three.
    expect(presented.record_count).toBe(40);
    expect(presented.event_estimate).toBe(30);
    expect(presented.collection_count).toBe(3);
    expect(presented.excluded_by_qc).toBe(2);
    expect(presented.possible_only_records).toBe(1);
    expect(presented.geometry.type).toBe("Polygon");
  });

  /**
   * The effort layer interpolates `event_estimate` into a colour, so a null would produce no
   * colour at all for a cell the plane did return.
   */
  it("reads an unreported event estimate as zero so the cell still takes a colour", () => {
    const presented = presentBotanicalCell(decodedCell({ eventEstimate: null }));
    expect(presented.event_estimate).toBe(0);
  });

  /**
   * An evaluation state the client has no union member for is CARRIED, not dropped: the richness
   * layer's `case` expression falls to its documented "never drawn as if it were data" default,
   * which is strictly better than the cell vanishing with nothing to say why.
   */
  it("carries an unrecognized evaluation state through rather than dropping the cell", () => {
    const presented = presentBotanicalCell(decodedCell({ evaluation: "some_future_state" }));
    expect(presented.evaluation).toBe("some_future_state");
  });
});
