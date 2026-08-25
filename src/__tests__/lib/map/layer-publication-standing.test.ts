import { describe, expect, it } from "vitest";
import {
  LAYER_PUBLICATION_STANDINGS,
  layerPublicationStandingCaption,
  type LayerPublicationStanding,
} from "@/lib/map/layer-publication-standing";
import { LAYER_REGISTRY, LAYER_TOGGLE_IDS, type LayerToggleId } from "@/lib/map/layer-registry";

/**
 * The invariant this file exists for: every toggle in the dock resolves either to a warehouse
 * lane that can fill it, or to a stated reason it will never be filled. Neither is optional, and
 * the failure mode when one is missing is silent -- the switch turns on, the map draws nothing,
 * and no surface anywhere says why. That is the state all four of the layers below shipped in.
 *
 * The two lists are HAND-SPELLED, deliberately, in the same discipline
 * `environmental-read-model.test.ts` uses for the stream catalogue: importing the record under
 * test to build the expectation would let a standing and its declaration drift together and
 * still pass. Adding a nineteenth toggle therefore fails here until it is classified.
 */

/** Toggles a Parquet lane backs (twelve lanes; the signal plane feeds twelve of these rows). */
const LANE_BACKED_TOGGLE_IDS: LayerToggleId[] = [
  "fire",
  "fire-perimeters",
  "water",
  "drought",
  "weather",
  "sensors",
  "watersheds",
  "vegetation",
  "soil-survey",
  "soil-moisture",
  "soil-temperature",
  "soil-vpd",
  "climate-air-temperature",
  "climate-dew-point",
  "climate-precipitation",
  "climate-relative-humidity",
  "climate-shortwave-radiation",
  "climate-wind-speed",
  "climate-soil-wetness-surface",
  "climate-soil-wetness-root-zone",
  "climate-soil-wetness-profile",
  "evacuation-zones",
  "burn-severity",
];

/**
 * The four surfaces no Parquet lane can ever back, measured against production 2026-08-25.
 *
 * `interventions` is a community feature that stays in Postgres by design; `strategy-
 * recommendations` needs an ML label plane that has no labels; `soil` is a raster with no
 * first-party release; `demand-heatmap` is derived at request time and stores nothing per day.
 * Inventing a producer for any of them is a track of its own, not a caption.
 */
const NON_LANE_TOGGLE_IDS: LayerToggleId[] = [
  "interventions",
  "strategy-recommendations",
  "soil",
  "demand-heatmap",
];

/** The one non-lane surface that is genuinely WITHHELD, so it is captioned by the registry. */
const WITHHELD_NON_LANE_TOGGLE_ID: LayerToggleId = "soil";

function standingOf(toggleId: LayerToggleId): LayerPublicationStanding | undefined {
  return LAYER_PUBLICATION_STANDINGS[toggleId];
}

describe("layer publication standing", () => {
  it("classifies every registry toggle as lane-backed or non-lane, with none left over", () => {
    const classified = [...LANE_BACKED_TOGGLE_IDS, ...NON_LANE_TOGGLE_IDS];
    expect(new Set(classified).size).toBe(classified.length);
    expect([...classified].sort()).toEqual([...LAYER_TOGGLE_IDS].sort());
  });

  it("gives every non-lane surface exactly one honest state, never both and never neither", () => {
    for (const toggleId of NON_LANE_TOGGLE_IDS) {
      const hasStanding = standingOf(toggleId) !== undefined;
      const isWithheld = LAYER_REGISTRY[toggleId].permanentlyUnavailableReason !== null;
      // XOR. Both would caption the row twice; neither is the silent blank map itself.
      expect({ toggleId, hasStanding, isWithheld }).toEqual({
        toggleId,
        hasStanding: toggleId !== WITHHELD_NON_LANE_TOGGLE_ID,
        isWithheld: toggleId === WITHHELD_NON_LANE_TOGGLE_ID,
      });
    }
  });

  // A standing is an admission that no lane will ever fill this surface. Leaving one on a layer
  // whose lane completes would caption a working layer with an excuse, which is the same class
  // of stale claim as the count rule below.
  it("leaves every lane-backed toggle without a standing", () => {
    const wrongly = LANE_BACKED_TOGGLE_IDS.filter((toggleId) => standingOf(toggleId) !== undefined);
    expect(wrongly).toEqual([]);
  });

  // The record's own rule, enforced rather than trusted: "two recommendations exist, both
  // approved" was true on 2026-08-25 and is wrong the next time one lands. A digit in either
  // sentence is a count or a date, and both go stale without anything reopening them.
  it("states no count and no date in any standing", () => {
    for (const toggleId of Object.keys(LAYER_PUBLICATION_STANDINGS) as LayerToggleId[]) {
      const standing = standingOf(toggleId);
      if (standing === undefined) throw new Error(`expected a standing for ${toggleId}`);
      expect({ toggleId, reason: /\d/.test(standing.reason) }).toEqual({ toggleId, reason: false });
      expect({ toggleId, unblockedBy: /\d/.test(standing.unblockedBy) }).toEqual({
        toggleId,
        unblockedBy: false,
      });
    }
  });

  it("names both the emptiness and what would end it, in one caption", () => {
    const standing = standingOf("interventions");
    if (standing === undefined) throw new Error("expected an interventions standing");
    const caption = layerPublicationStandingCaption("interventions");
    expect(caption).toBe(`${standing.reason} ${standing.unblockedBy}`);
    // The two halves are a different claim each: why it is empty, and what is holding it there.
    expect(standing.reason).not.toBe(standing.unblockedBy);
  });

  it("answers null for a lane-backed layer, so a working row gets no caption", () => {
    expect(layerPublicationStandingCaption("vegetation")).toBeNull();
  });

  // The split that keeps a standing OUT of the governance gate: `permanentlyUnavailableReason`
  // disables the switch and reads false in `useLayerVisibility` whatever `activeLayers` says.
  // These three layers have live renderers that would paint the moment a row appeared.
  it("leaves the three standing layers switchable", () => {
    for (const toggleId of NON_LANE_TOGGLE_IDS) {
      if (toggleId === WITHHELD_NON_LANE_TOGGLE_ID) continue;
      expect(LAYER_REGISTRY[toggleId].permanentlyUnavailableReason).toBeNull();
    }
  });
});
