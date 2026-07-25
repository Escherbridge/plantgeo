import { describe, expect, it } from "vitest";
import { canonicalViewportBbox, viewportBbox } from "@/lib/map/viewport-bbox";

describe("viewport bbox contract", () => {
  it("uses the API's west,south,east,north order for ordinary map viewports", () => {
    expect(viewportBbox(-105, 40, 8)).toBe("-107.8125,38.59375,-102.1875,41.40625");
  });

  it("normalizes wrapped world copies before querying a provider", () => {
    expect(viewportBbox(255, 40, 8)).toBe("-107.8125,38.59375,-102.1875,41.40625");
  });

  it("refuses antimeridian and malformed windows that the API cannot represent", () => {
    expect(viewportBbox(180, 40, 8)).toBeNull();
    expect(viewportBbox(-105, 40, 2)).toBeNull();
    expect(canonicalViewportBbox("170,10,190,20")).toBeNull();
    expect(canonicalViewportBbox("0,10,0,20")).toBeNull();
  });
});
