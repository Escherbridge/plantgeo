import { describe, expect, it } from "vitest";
import { bboxTupleForFlowQuery } from "@/components/map/visualization/intervention-flow-viewport";

describe("bboxTupleForFlowQuery", () => {
  it("returns null when the viewport has not reported a bbox yet", () => {
    expect(bboxTupleForFlowQuery(null)).toBeNull();
  });

  it("returns null for a string that does not parse into four finite numbers", () => {
    expect(bboxTupleForFlowQuery("not,a,bbox")).toBeNull();
    expect(bboxTupleForFlowQuery("-116,44,-117")).toBeNull();
    expect(bboxTupleForFlowQuery("-116,44,NaN,45")).toBeNull();
  });

  it("returns null for a degenerate box (west >= east, or south >= north)", () => {
    expect(bboxTupleForFlowQuery("-116,44,-117,45")).toBeNull();
    expect(bboxTupleForFlowQuery("-117,45,-116,44")).toBeNull();
  });

  it("returns null for a box outside latitude range", () => {
    expect(bboxTupleForFlowQuery("-117,-95,-116,44")).toBeNull();
    expect(bboxTupleForFlowQuery("-117,43,-116,95")).toBeNull();
  });

  it("parses a valid, already-canonical bbox into its tuple", () => {
    expect(bboxTupleForFlowQuery("-117,43,-116,44")).toEqual([-117, 43, -116, 44]);
  });

  it("normalizes a wrapped longitude the same way canonicalViewportBbox does", () => {
    // 190 wraps to -170 under the (((x+180)%360)+360)%360-180 normalization.
    expect(bboxTupleForFlowQuery("190,43,195,44")).toEqual([-170, 43, -165, 44]);
  });
});
