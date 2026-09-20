/**
 * What the binding rule does when its two compiled-in enumerations disagree with each other.
 *
 * `layer-region-binding.test.tsx` pins `REGION_LAYER_SLUG_BY_WAREHOUSE_NAME`'s values to the
 * manifest's `platformLayers`, which is how a typo is meant to be caught. This file proves the
 * behaviour when it is not caught anyway -- a deleted test, a manifest that drops a layer, a region
 * whose vocabulary is narrower than this bundle's tables. `not_federated` is treated as AVAILABLE
 * (`layer-region-binding.ts`), so answering it for a slug this build itself asks about would draw
 * the outage-shaped empty map `federation.md` §2 forbids (STYLE-REVIEW-W6 S1).
 *
 * Lives in its own file because it needs a fabricated region: `getRegion()` memoises the compiled
 * PNW manifest, and the rest of the suite must keep reading the real one.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A region missing `drought` from its vocabulary while this bundle still asks about it -- the shape
// a one-character drift in either table produces. Spelled INSIDE the factory because `vi.mock` is
// hoisted above every `const` in the file. Only the three fields `layerBindingInRegion` reads.
vi.mock("@/lib/region/region", () => ({
  getRegion: () => ({
    slug: "fabricated-narrow-vocabulary",
    platformLayers: ["soil-survey", "signal"],
    enabledLayers: [{ layerSlug: "soil-survey", sourceSlug: "ssurgo", coverage: "regional" }],
  }),
}));

describe("layerBindingInRegion when the compiled tables disagree", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reports a compiled-in slug that is absent from platformLayers as unbound, not not_federated", async () => {
    const { layerBindingInRegion, TOGGLE_REACHABLE_REGION_LAYER_SLUGS } = await import(
      "@/lib/map/layer-region-binding"
    );
    expect(TOGGLE_REACHABLE_REGION_LAYER_SLUGS.has("drought")).toBe(true);

    expect(layerBindingInRegion(null, "drought")).toBe("unbound");
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining("drought"));
  });

  it("reports the land-context slug the same way, since a compiled-in caller names it too", async () => {
    const { layerBindingInRegion, LAND_CONTEXT_REGION_LAYER_SLUG } = await import(
      "@/lib/map/layer-region-binding"
    );

    expect(layerBindingInRegion(null, LAND_CONTEXT_REGION_LAYER_SLUG)).toBe("unbound");
  });

  it("still calls a slug from outside this build not_federated, and logs nothing", async () => {
    const { layerBindingInRegion } = await import("@/lib/map/layer-region-binding");

    expect(layerBindingInRegion(null, "a-layer-this-build-never-heard-of")).toBe("not_federated");
    expect(console.error).not.toHaveBeenCalled();
  });
});
