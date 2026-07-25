import { describe, expect, it } from "vitest";
import { readGeocodingResults } from "@/lib/geocoding";

const result = {
  id: "result-1",
  type: "city",
  name: "Denver",
  displayName: "Denver, Colorado, United States",
  coordinates: [-104.9903, 39.7392],
  properties: { country: "United States" },
};

describe("browser geocoding response contract", () => {
  it("accepts bounded normalized results with GeoJSON longitude,latitude order", async () => {
    await expect(
      readGeocodingResults(
        new Response(JSON.stringify({ results: [result] }), { status: 200 })
      )
    ).resolves.toEqual([result]);
  });

  it("propagates an API failure instead of treating it as an empty result", async () => {
    await expect(
      readGeocodingResults(new Response(JSON.stringify({ error: "rate limited" }), { status: 429 }))
    ).rejects.toThrow("Geocode request failed: 429");
  });

  it("rejects malformed or latitude,longitude-swapped browser payloads", async () => {
    await expect(
      readGeocodingResults(
        new Response(
          JSON.stringify({ results: [{ ...result, coordinates: [39.7392, -104.9903] }] }),
          { status: 200 }
        )
      )
    ).rejects.toThrow("expected result contract");
  });
});
