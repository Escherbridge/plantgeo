/**
 * The coverage census names its own region, and a disagreement is refused rather than drawn.
 *
 * `PLANTGEO_REGION` (service) and `NEXT_PUBLIC_PLANTGEO_REGION` (bundle) are two independently
 * settable variables naming one fact. Until 2026-09-18 nothing on the wire said whose region the
 * census described, so a deployment that set one and not the other served one region's footprint
 * over the other region's data undetectably (STYLE-REVIEW-W8 S1). The Python half is
 * `tests/interface/test_parquet_routes.py::test_the_census_names_the_region_whose_footprint_it_describes`.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { layerBindingInRegion } from "@/lib/map/layer-region-binding";
import { getRegion, regionIdentityVerdict } from "@/lib/region/region";
import type { SliderCapabilities } from "@/types/time-slider";

/** A payload that STATES `soil-survey` is bound, which the pilot manifest also says. */
function capabilitiesFrom(servedRegionSlug: string | null | undefined): SliderCapabilities {
  return {
    serverCurrentDate: "2026-09-18",
    futureAxisDays: 2,
    streamsUnavailable: false,
    layers: [],
    layerBindings: [
      { layerSlug: "land-context", binding: "bound_regional", sourceSlug: "someone-elses", reason: null },
    ],
    servedRegionSlug,
  };
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("regionIdentityVerdict", () => {
  it("reads silence as no claim, never as agreement with the pilot", () => {
    expect(regionIdentityVerdict(undefined)).toEqual({ kind: "unstated", compiledSlug: getRegion().slug });
    expect(regionIdentityVerdict(null)).toEqual({ kind: "unstated", compiledSlug: getRegion().slug });
  });

  it("agrees with its own slug and names both sides of a disagreement", () => {
    expect(regionIdentityVerdict(getRegion().slug)).toEqual({ kind: "agrees", slug: getRegion().slug });
    expect(regionIdentityVerdict("kenya-highlands")).toEqual({
      kind: "mismatch",
      compiledSlug: "pnw",
      servedSlug: "kenya-highlands",
    });
  });

  it("follows the SELECTED region rather than the pilot", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "kenya-highlands");
    expect(regionIdentityVerdict("kenya-highlands").kind).toBe("agrees");
    expect(regionIdentityVerdict("pnw").kind).toBe("mismatch");
  });
});

describe("layerBindingInRegion when the census names another region", () => {
  it("refuses the payload's bindings and answers from the compiled manifest", () => {
    // `land-context` is a platform layer no manifest binds, so the compiled answer is `unbound`.
    // Trusting the foreign payload would have answered `bound` -- another region's binding read as
    // this one's, which is exactly the silent wrong-region render this field exists to stop.
    expect(layerBindingInRegion(capabilitiesFrom("kenya-highlands"), "land-context")).toBe("unbound");
  });

  it("still trusts a payload that agrees, and one that states no region at all", () => {
    expect(layerBindingInRegion(capabilitiesFrom(getRegion().slug), "land-context")).toBe("bound");
    expect(layerBindingInRegion(capabilitiesFrom(undefined), "land-context")).toBe("bound");
  });
});
