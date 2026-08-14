import { describe, expect, it } from "vitest";
import { lon2tile, lat2tile, calculateTileUrls, getStorageEstimateMB } from "@/lib/offline/tile-cache";

describe("Offline Tile Cache & Coordinates", () => {
  it("converts longitude and latitude to tile numbers accurately", () => {
    const x = lon2tile(-122.4, 10);
    const y = lat2tile(37.7, 10);
    expect(typeof x).toBe("number");
    expect(typeof y).toBe("number");
    expect(x).toBeGreaterThan(0);
    expect(y).toBeGreaterThan(0);
  });

  it("calculates tile URLs for a given bounding box and zoom range", () => {
    const bbox = { west: -123, south: 45, east: -122, north: 46 };
    const urls = calculateTileUrls(bbox, 5, 6, 50);

    expect(urls.length).toBeGreaterThan(0);
    expect(urls[0]).toContain("arcgisonline.com");
  });

  it("returns storage estimate object gracefully", async () => {
    const storage = await getStorageEstimateMB();
    expect(storage).toHaveProperty("usageMB");
    expect(storage).toHaveProperty("quotaMB");
  });
});
