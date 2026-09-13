import { describe, expect, it } from "vitest";
import { decodeBoundaryGeometry } from "../boundary-geometry-adapter";
import { WkbDecodeError } from "../wkb-decode-error";
import type { BoundaryVersionRef } from "../../types";

function makeRef(geometryWkb: string | null): BoundaryVersionRef {
  return {
    sourceNamespace: "wa-king-county-assessor",
    nativeFeatureKey: "0012345",
    nativeFeatureVersion: null,
    familyType: "parcel",
    interestType: "fee_parcel",
    state: "WA",
    county: "King",
    geometryWkb,
  };
}

describe("decodeBoundaryGeometry", () => {
  it("returns null (not a throw) when geometryWkb is null", () => {
    expect(decodeBoundaryGeometry(makeRef(null))).toBeNull();
  });

  it("decodes a present geometryWkb into GeoJSON", () => {
    const ref = makeRef("0101000000000000000000f03f0000000000000040");
    expect(decodeBoundaryGeometry(ref)).toEqual({ type: "Point", coordinates: [1, 2] });
  });

  it("still throws WkbDecodeError for malformed-but-present geometryWkb", () => {
    const ref = makeRef("not-hex");
    expect(() => decodeBoundaryGeometry(ref)).toThrow(WkbDecodeError);
  });
});
