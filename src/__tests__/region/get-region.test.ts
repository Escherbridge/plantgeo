import { describe, expect, it } from "vitest";
import { getRegion, regionSchema } from "@/lib/region/region";
import { PNW } from "@/lib/region/pnw";

describe("getRegion", () => {
  it("returns the PNW pilot manifest", () => {
    expect(getRegion()).toBe(PNW);
    expect(getRegion().slug).toBe("pnw");
  });

  it("parses against its own Zod schema", () => {
    expect(() => regionSchema.parse(getRegion())).not.toThrow();
  });

  it("rejects an envelope with west >= east", () => {
    const invalid = { ...getRegion().envelope, west: getRegion().envelope.east };
    expect(() => regionSchema.parse({ ...getRegion(), envelope: invalid })).toThrow();
  });

  it("carries the eleven geo.layers slugs from layer-lanes.md §1", () => {
    const slugs = getRegion().enabledLayers.map((binding) => binding.layerSlug);
    expect(slugs).toEqual([
      "soil-survey",
      "fire-detections",
      "vegetation",
      "burn-severity",
      "evacuation-zones",
      "interventions",
      "fire-perimeters",
      "water-gauges",
      "sensors",
      "weather-observations",
      "watersheds",
    ]);
  });
});
