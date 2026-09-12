import { describe, expect, it } from "vitest";
import {
  POI_CATEGORIES,
  escapeLikeWildcards,
  getById,
  searchByCategory,
  searchByText,
  searchNearby,
} from "@/lib/server/services/places";

const BBOX = { west: -122.8, south: 45.4, east: -122.5, north: 45.6 };

describe("Parquet-only places contract", () => {
  it("returns typed empty results without opening a retired PostgreSQL POI table", async () => {
    await expect(searchByCategory("parks", BBOX)).resolves.toEqual({ places: [], truncated: false });
    await expect(searchByText("park", BBOX)).resolves.toEqual({ places: [], truncated: false });
    await expect(searchNearby(45.52, -122.68, 2_000, 20)).resolves.toEqual({
      places: [],
      truncated: false,
    });
    await expect(getById("00000000-0000-4000-8000-000000000000")).resolves.toBeNull();
  });

  it("keeps the public category and escaping contracts while the lane is unadmitted", () => {
    expect(POI_CATEGORIES.map(({ id }) => id)).toContain("parks");
    expect(escapeLikeWildcards("100% off_beaten\\path")).toBe("100\\% off\\_beaten\\\\path");
  });
});
