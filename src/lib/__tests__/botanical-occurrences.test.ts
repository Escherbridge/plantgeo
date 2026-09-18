// S9, W3 review: `BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST` used to be hand-listed with
// `satisfies readonly BotanicalSupportBand[]`, which checks each LISTED element is a valid band
// but not that every band IS listed -- a fourth band added to `BotanicalSupportBand` could
// silently fall off the ladder. It is now derived from `BOTANICAL_BBOX_CEILING_SQUARE_DEGREES`
// (a `Record<BotanicalSupportBand, number>`, exhaustive-checked by the compiler); this test pins
// the derived ladder's membership, order and correspondence to the ceiling map.
import { describe, expect, it } from "vitest";
import {
  BOTANICAL_BBOX_CEILING_SQUARE_DEGREES,
  BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST,
  type BotanicalSupportBand,
} from "@/lib/botanical-occurrences";

const ALL_BANDS: readonly BotanicalSupportBand[] = ["detail", "grid-0.05", "grid-0.25"];

describe("BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST", () => {
  it("contains exactly every band in the union, no more and no fewer", () => {
    expect(new Set(BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST)).toEqual(new Set(ALL_BANDS));
    expect(BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST).toHaveLength(ALL_BANDS.length);
  });

  it("is ordered coarsest first: each rung's ceiling is not smaller than the next rung's", () => {
    for (let index = 1; index < BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST.length; index += 1) {
      const previousCeiling =
        BOTANICAL_BBOX_CEILING_SQUARE_DEGREES[BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST[index - 1]];
      const currentCeiling =
        BOTANICAL_BBOX_CEILING_SQUARE_DEGREES[BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST[index]];
      expect(previousCeiling).toBeGreaterThanOrEqual(currentCeiling);
    }
  });

  it("matches the ladder this repo has always declared, exactly", () => {
    expect(BOTANICAL_SUPPORT_BANDS_COARSEST_FIRST).toEqual(["grid-0.25", "grid-0.05", "detail"]);
  });
});
