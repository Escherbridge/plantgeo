import { describe, expect, it } from "vitest";
import {
  attachDecodedGeometries,
  attachDecodedGeometry,
  attachDecodedGeometryToOne,
} from "../attach-decoded-geometry";
import { estimateResponseBytes, MAX_RESPONSE_BYTES } from "../../budgets";
import type { BoundaryVersionRef, LandContextResult } from "../../types";

/** Little-endian plain-WKB Polygon with one ring of `ringLength` vertices, as a parcel lane would store it. */
function polygonWkb(ringLength: number): string {
  const buffer = Buffer.alloc(1 + 4 + 4 + 4 + ringLength * 16);
  let offset = 0;
  buffer.writeUInt8(1, offset); offset += 1;
  buffer.writeUInt32LE(3, offset); offset += 4;
  buffer.writeUInt32LE(1, offset); offset += 4;
  buffer.writeUInt32LE(ringLength, offset); offset += 4;
  for (let i = 0; i < ringLength; i += 1) {
    const t = (i / ringLength) * Math.PI * 2;
    buffer.writeDoubleLE(-116.2 + Math.cos(t) * 0.01, offset); offset += 8;
    buffer.writeDoubleLE(43.6 + Math.sin(t) * 0.01, offset); offset += 8;
  }
  return buffer.toString("hex");
}

/** POINT(1 2), little-endian plain WKB -- the same bytes `boundary-geometry-adapter.test.ts` uses. */
const POINT_WKB = "0101000000000000000000f03f0000000000000040";

function makeRef(geometryWkb: string | null): BoundaryVersionRef {
  return {
    sourceNamespace: "blm-national-sma",
    nativeFeatureKey: "SMA-000123",
    nativeFeatureVersion: null,
    familyType: "blm_surface_management",
    interestType: "surface_management",
    state: "ID",
    county: "Ada",
    geometryWkb,
  };
}

function matched(sourceFeature: BoundaryVersionRef | null, gaps: string[] = []): LandContextResult {
  return {
    coverageState: "matched",
    sourceFeature,
    sourceRelease: null,
    matchedRegionOrOverlap: { kind: "point_containment", description: "point inside polygon" },
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: gaps,
    isCurrentReferenceOnly: true,
  };
}

describe("attachDecodedGeometry", () => {
  it("decodes a present geometryWkb into GeoJSON, strips the hex, and leaves every other field untouched", () => {
    const input = matched(makeRef(POINT_WKB));
    const output = attachDecodedGeometry(input);
    expect(output.geometry).toEqual({ type: "Point", coordinates: [1, 2] });
    // Geometry travels as GeoJSON only: the hex the browser never reads is nulled on the wire.
    expect(output.sourceFeature?.geometryWkb).toBeNull();
    const { geometryWkb: _inputWkb, ...inputRef } = input.sourceFeature!;
    const { geometryWkb: _outputWkb, ...outputRef } = output.sourceFeature!;
    expect(outputRef).toEqual(inputRef);
    expect({ ...output, sourceFeature: undefined, geometry: undefined }).toEqual({
      ...input,
      sourceFeature: undefined,
      geometry: undefined,
    });
    expect(Object.keys(output).sort()).toEqual([...Object.keys(input), "geometry"].sort());
    // The input is not mutated.
    expect(input.sourceFeature?.geometryWkb).toBe(POINT_WKB);
  });

  it("keeps a budgeted response the same order of bytes on the wire rather than doubling it", () => {
    // 100 features of a 300-vertex ring (~1 MB of hex): the reader budgets these WITH the hex.
    const results = Array.from({ length: 100 }, () => matched(makeRef(polygonWkb(300))));
    const budgeted = estimateResponseBytes(results);
    expect(budgeted).toBeLessThan(MAX_RESPONSE_BYTES);

    const shipped = estimateResponseBytes(attachDecodedGeometries({ status: "ok", data: results }));
    // Decoded GeoJSON is somewhat larger than 16-hex-chars-per-double, never the hex PLUS GeoJSON.
    expect(shipped).toBeLessThan(budgeted * 1.5);
    expect(shipped).toBeLessThan(MAX_RESPONSE_BYTES);
  });

  it("yields geometry: null for a null geometryWkb and states no new gap (null is 'no data', not a defect)", () => {
    const output = attachDecodedGeometry(matched(makeRef(null), ["existing gap"]));
    expect(output.geometry).toBeNull();
    expect(output.unresolvedGaps).toEqual(["existing gap"]);
  });

  it("yields geometry: null when the result has no sourceFeature at all", () => {
    const output = attachDecodedGeometry({ ...matched(null), coverageState: "unknown_coverage" });
    expect(output.geometry).toBeNull();
  });

  it("nulls the geometry and states the defect in unresolvedGaps for malformed WKB instead of throwing", () => {
    const output = attachDecodedGeometry(matched(makeRef("not-hex"), ["existing gap"]));
    expect(output.geometry).toBeNull();
    expect(output.unresolvedGaps).toHaveLength(2);
    expect(output.unresolvedGaps[0]).toBe("existing gap");
    expect(output.unresolvedGaps[1]).toMatch(/^boundary geometry present but undecodable \(invalid_hex\)/);
    // The record is still a matched, listable feature -- only its shape is missing, and said so.
    expect(output.coverageState).toBe("matched");
    expect(output.sourceFeature?.nativeFeatureKey).toBe("SMA-000123");
    // The bad hex is not shipped either; the gap is the record of it.
    expect(output.sourceFeature?.geometryWkb).toBeNull();
  });
});

describe("attachDecodedGeometries / attachDecodedGeometryToOne", () => {
  it("maps every ok result", () => {
    const response = attachDecodedGeometries({
      status: "ok",
      data: [matched(makeRef(POINT_WKB)), matched(makeRef(null))],
    });
    expect(response.status).toBe("ok");
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data.map((result) => result.geometry)).toEqual([
      { type: "Point", coordinates: [1, 2] },
      null,
    ]);
  });

  it("passes a budget_exceeded response through untouched", () => {
    const refused = {
      status: "budget_exceeded" as const,
      reason: "aoi_area_exceeds_limit" as const,
      limit: 1,
      requested: 2,
    };
    expect(attachDecodedGeometries(refused)).toBe(refused);
    expect(attachDecodedGeometryToOne(refused)).toBe(refused);
  });

  it("wraps a single ok result", () => {
    const response = attachDecodedGeometryToOne({ status: "ok", data: matched(makeRef(POINT_WKB)) });
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data.geometry).toEqual({ type: "Point", coordinates: [1, 2] });
  });
});
