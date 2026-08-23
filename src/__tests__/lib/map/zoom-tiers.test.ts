import { describe, expect, it } from "vitest";
import {
  ZOOM_TIERS,
  ZoomTierResolutionError,
  resolveZoomTier,
  zoomTierPathSegment,
} from "@/lib/map/zoom-tiers";

describe("the uniform zoom ladder", () => {
  it("publishes exactly z0, z5, z9, z13 in ascending order", () => {
    expect(ZOOM_TIERS).toEqual([0, 5, 9, 13]);
  });
});

describe("resolveZoomTier at exact breakpoints", () => {
  it("resolves z0 to the z0 tier", () => {
    expect(resolveZoomTier(0)).toBe(0);
  });

  it("resolves z5 to the z5 tier", () => {
    expect(resolveZoomTier(5)).toBe(5);
  });

  it("resolves z9 to the z9 tier", () => {
    expect(resolveZoomTier(9)).toBe(9);
  });

  it("resolves z13 to the z13 tier", () => {
    expect(resolveZoomTier(13)).toBe(13);
  });
});

describe("resolveZoomTier just below each breakpoint", () => {
  it("resolves the value just under z5 to the z0 tier", () => {
    expect(resolveZoomTier(4.999)).toBe(0);
  });

  it("resolves the value just under z9 to the z5 tier", () => {
    expect(resolveZoomTier(8.999)).toBe(5);
  });

  it("resolves the value just under z13 to the z9 tier", () => {
    expect(resolveZoomTier(12.999)).toBe(9);
  });
});

describe("resolveZoomTier just above each breakpoint", () => {
  it("resolves the value just over z0 to the z0 tier", () => {
    expect(resolveZoomTier(0.001)).toBe(0);
  });

  it("resolves the value just over z5 to the z5 tier", () => {
    expect(resolveZoomTier(5.001)).toBe(5);
  });

  it("resolves the value just over z9 to the z9 tier", () => {
    expect(resolveZoomTier(9.001)).toBe(9);
  });

  it("resolves the value just over z13 to the z13 tier", () => {
    expect(resolveZoomTier(13.001)).toBe(13);
  });
});

describe("resolveZoomTier on ordinary fractional zooms", () => {
  it("resolves a mid-tier fractional zoom to its enclosing tier, matching the MapLibre-reported example", () => {
    expect(resolveZoomTier(11.4)).toBe(9);
  });

  it("resolves a fractional zoom below the first breakpoint to the floor tier", () => {
    expect(resolveZoomTier(3)).toBe(0);
  });
});

describe("resolveZoomTier above the top tier", () => {
  it("resolves a zoom far past the top tier to the top tier rather than throwing", () => {
    expect(resolveZoomTier(22)).toBe(13);
  });

  it("resolves an extreme zoom to the top tier", () => {
    expect(resolveZoomTier(100)).toBe(13);
  });
});

describe("resolveZoomTier below the floor tier", () => {
  it("throws for a negative zoom rather than silently serving z0", () => {
    expect(() => resolveZoomTier(-1)).toThrow(ZoomTierResolutionError);
  });

  it("throws for a small negative fractional zoom", () => {
    expect(() => resolveZoomTier(-0.001)).toThrow(ZoomTierResolutionError);
  });
});

describe("resolveZoomTier on non-finite input", () => {
  it("throws for NaN rather than silently serving a tier", () => {
    expect(() => resolveZoomTier(NaN)).toThrow(ZoomTierResolutionError);
  });

  it("throws for positive Infinity", () => {
    expect(() => resolveZoomTier(Infinity)).toThrow(ZoomTierResolutionError);
  });

  it("throws for negative Infinity", () => {
    expect(() => resolveZoomTier(-Infinity)).toThrow(ZoomTierResolutionError);
  });
});

describe("zoomTierPathSegment", () => {
  it("zero-pads single-digit tiers to two digits, matching zoom_prefix in zoom.py", () => {
    expect(zoomTierPathSegment(0)).toBe("zoom=00");
    expect(zoomTierPathSegment(5)).toBe("zoom=05");
    expect(zoomTierPathSegment(9)).toBe("zoom=09");
  });

  it("renders the two-digit top tier unpadded beyond its natural width", () => {
    expect(zoomTierPathSegment(13)).toBe("zoom=13");
  });
});
