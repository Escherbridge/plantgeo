import { describe, expect, it } from "vitest";
import { getRegion, regionSchema } from "@/lib/region/region";

describe("getRegion", () => {
  it("returns the PNW pilot manifest", () => {
    expect(getRegion().slug).toBe("pnw");
  });

  it("parses against its own Zod schema", () => {
    expect(() => regionSchema.parse(getRegion())).not.toThrow();
  });

  it("rejects an envelope with west >= east", () => {
    const invalid = { ...getRegion().envelope, west: getRegion().envelope.east };
    expect(() => regionSchema.parse({ ...getRegion(), envelope: invalid })).toThrow();
  });

  it("memoises: two calls return the identical parsed object", () => {
    // Proves `getRegion()` parses through `regionSchema` once and caches the result
    // (STYLE-REVIEW-W1.md S1), not once per call.
    expect(getRegion()).toBe(getRegion());
  });

  it("deep-freezes the returned manifest", () => {
    // STYLE-REVIEW-W1.md S2: a caller aliasing a nested value (as `coverage-region.ts` does for
    // `defaultCameraEnvelope`) must not be able to mutate the manifest through that alias.
    const region = getRegion();
    expect(Object.isFrozen(region)).toBe(true);
    expect(Object.isFrozen(region.envelope)).toBe(true);
    expect(Object.isFrozen(region.enabledLayers)).toBe(true);
    expect(Object.isFrozen(region.enabledLayers[0])).toBe(true);
    expect(() => {
      (region.defaultCameraEnvelope as { west: number }).west = 0;
    }).toThrow();
  });

  it("declares the admitted pilot sources without treating interventions as an environmental lane", () => {
    const slugs = getRegion().enabledLayers.map((binding) => binding.layerSlug);
    expect(slugs).toEqual([
      "land-context",
      "crop-cover",
      "soil-survey",
      "fire-detections",
      "vegetation",
      "burn-severity",
      "evacuation-zones",
      "fire-perimeters",
      "water-gauges",
      "sensors",
      "weather-observations",
      "watersheds",
      "drought",
      "climate-field-air-temperature",
      "climate-field-dew-point",
      "climate-field-precipitation",
      "climate-field-relative-humidity",
      "climate-field-shortwave-radiation",
      "climate-field-soil-wetness-profile",
      "climate-field-soil-wetness-root-zone",
      "climate-field-soil-wetness-surface",
      "climate-field-wind-speed",
      "soil-field-moisture",
      "soil-field-temperature",
      "soil-field-vpd",
      "botanical-occurrences",
    ]);
    expect(slugs).not.toContain("interventions");
    expect(getRegion().enabledLayers.find((binding) => binding.layerSlug === "land-context")?.sourceSlug).toBe("blm_surface_management");
    expect(getRegion().enabledLayers.find((binding) => binding.layerSlug === "crop-cover")?.sourceSlug).toBe("usda_cdl");
  });
});
