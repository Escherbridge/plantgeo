// S8, W3 review: `selectFinestAdmittingRung` returned `null` for two different failures --
// "no rung on the ladder admits this area" and "`finestAllowed` names a rung that is not even on
// the ladder", a configuration defect. `selectFinestAdmittingRungResult` names each distinctly.
import { describe, expect, it } from "vitest";
import {
  selectFinestAdmittingRung,
  selectFinestAdmittingRungResult,
  type RungSelectionRequest,
} from "@/lib/map/rung-selection";

const LADDER = [0, 5, 9, 13] as const;
const CEILINGS: Record<(typeof LADDER)[number], number> = { 0: 1600, 5: 100, 9: 4, 13: 0.25 };

function request(
  overrides: Partial<RungSelectionRequest<(typeof LADDER)[number]>> = {}
): RungSelectionRequest<(typeof LADDER)[number]> {
  return {
    coarsestFirst: LADDER,
    maxBboxSquareDegrees: CEILINGS,
    areaSquareDegrees: 50,
    ...overrides,
  };
}

describe("selectFinestAdmittingRungResult", () => {
  it("names 'selected' with the chosen rung when one admits the area", () => {
    expect(selectFinestAdmittingRungResult(request({ areaSquareDegrees: 0.1 }))).toEqual({
      kind: "selected",
      rung: 13,
    });
    expect(selectFinestAdmittingRungResult(request({ areaSquareDegrees: 50 }))).toEqual({
      kind: "selected",
      rung: 5,
    });
  });

  it("names 'no_rung_admits_area' when every rung's ceiling is too small, never a guessed rung", () => {
    expect(selectFinestAdmittingRungResult(request({ areaSquareDegrees: 999_999 }))).toEqual({
      kind: "no_rung_admits_area",
    });
  });

  it("names 'rung_not_on_ladder' -- distinctly from an area refusal -- when finestAllowed is not on the ladder", () => {
    // 7 is not a member of LADDER: a configuration defect, not "this viewport is too wide".
    const result = selectFinestAdmittingRungResult(
      request({ finestAllowed: 7 as (typeof LADDER)[number], areaSquareDegrees: 0.1 })
    );
    expect(result).toEqual({ kind: "rung_not_on_ladder", rung: 7 });
  });

  it("agrees with the bare selectFinestAdmittingRung on the 'selected' rung", () => {
    const bareRung = selectFinestAdmittingRung(request({ areaSquareDegrees: 50 }));
    const result = selectFinestAdmittingRungResult(request({ areaSquareDegrees: 50 }));
    expect(result).toEqual({ kind: "selected", rung: bareRung });
  });
});
