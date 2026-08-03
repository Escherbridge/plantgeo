import { afterEach, describe, expect, it, vi } from "vitest";

const PNW_BBOX = "-125,42,-111,49";

vi.mock("@/lib/server/services/ingest", () => ({
  ingestFeatures: vi.fn().mockResolvedValue(0),
}));
vi.mock("@/lib/server/services/nasa-firms", () => ({
  fetchActiveFiresNASA: vi.fn(),
}));
vi.mock("@/lib/server/services/usgs-water", () => ({
  getStreamflowGauges: vi.fn(),
}));
vi.mock("@/lib/server/services/weather", () => ({
  getCurrentWeather: vi.fn(),
}));
vi.mock("@/lib/server/services/wfigs-fire-perimeters", () => ({
  fetchWfigsFirePerimeters: vi.fn(),
}));

import { ingestFeatures } from "@/lib/server/services/ingest";
import { fetchActiveFiresNASA } from "@/lib/server/services/nasa-firms";
import { getCurrentWeather } from "@/lib/server/services/weather";
import {
  runFireIngestionJob,
  runWeatherIngestionJob,
} from "@/lib/server/services/ingestion-jobs";

/** Builds a synthetic FIRMS feature collection with the given number of detections. */
function firmsCollection(count: number) {
  const now = new Date().toISOString();
  return {
    type: "FeatureCollection" as const,
    features: Array.from({ length: count }, (_, index) => ({
      type: "Feature" as const,
      geometry: {
        type: "Point" as const,
        coordinates: [-120 + index * 0.0001, 45 + index * 0.0001] as [
          number,
          number,
        ],
      },
      properties: {
        brightness: 300,
        confidence: "high",
        frp: 1,
        satellite: "N",
        acqDate: now.slice(0, 10),
        acqTime: "1200",
        observedAt: now,
      },
    })),
  };
}

describe("ingestion job coverage bounds", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  describe("FIRMS source record cap", () => {
    it("defaults to 10,000 when INGEST_MAX_SOURCE_RECORDS is unset", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.mocked(fetchActiveFiresNASA).mockResolvedValue(firmsCollection(10_500));

      const result = await runFireIngestionJob();

      expect(result.recordsSeen).toBe(10_500);
      expect(result.truncated).toBe(true);
      const ingested = vi.mocked(ingestFeatures).mock.calls[0][0];
      expect(ingested).toHaveLength(10_000);
    });

    it("clamps a too-low INGEST_MAX_SOURCE_RECORDS up to 1,000", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.stubEnv("INGEST_MAX_SOURCE_RECORDS", "10");
      vi.mocked(fetchActiveFiresNASA).mockResolvedValue(firmsCollection(1_500));

      const result = await runFireIngestionJob();

      expect(result.truncated).toBe(true);
      const ingested = vi.mocked(ingestFeatures).mock.calls[0][0];
      expect(ingested).toHaveLength(1_000);
    });

    it("clamps a too-high INGEST_MAX_SOURCE_RECORDS down to 50,000", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.stubEnv("INGEST_MAX_SOURCE_RECORDS", "1000000");
      vi.mocked(fetchActiveFiresNASA).mockResolvedValue(firmsCollection(6_297));

      const result = await runFireIngestionJob();

      expect(result.truncated).toBe(false);
      const ingested = vi.mocked(ingestFeatures).mock.calls[0][0];
      expect(ingested).toHaveLength(6_297);
    });

    it("falls back to the default when INGEST_MAX_SOURCE_RECORDS is garbage", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.stubEnv("INGEST_MAX_SOURCE_RECORDS", "not-a-number");
      vi.mocked(fetchActiveFiresNASA).mockResolvedValue(firmsCollection(10_500));

      const result = await runFireIngestionJob();

      expect(result.truncated).toBe(true);
      const ingested = vi.mocked(ingestFeatures).mock.calls[0][0];
      expect(ingested).toHaveLength(10_000);
    });

    it("only sets truncated when records seen exceed the cap", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.stubEnv("INGEST_MAX_SOURCE_RECORDS", "5000");
      vi.mocked(fetchActiveFiresNASA).mockResolvedValue(firmsCollection(5_000));

      const result = await runFireIngestionJob();

      expect(result.recordsSeen).toBe(5_000);
      expect(result.truncated).toBe(false);
    });
  });

  describe("weather sample grid densification", () => {
    it("covers the PNW bbox with 98 points at default spacing, all inside the bbox", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.mocked(getCurrentWeather).mockResolvedValue({
        observedAt: new Date().toISOString(),
        temperature: 10,
        humidity: 50,
        windSpeed: 1,
        windDirection: 0,
        precipitation: 0,
      });

      await runWeatherIngestionJob();

      const calls = vi.mocked(getCurrentWeather).mock.calls;
      expect(calls).toHaveLength(98);
      for (const [lat, lon] of calls) {
        expect(lat).toBeGreaterThan(42);
        expect(lat).toBeLessThan(49);
        expect(lon).toBeGreaterThan(-125);
        expect(lon).toBeLessThan(-111);
      }
    });

    it("caps total points at 150 for a tiny spacing while still spanning the full extent", async () => {
      vi.stubEnv("INGEST_BBOX", PNW_BBOX);
      vi.stubEnv("WEATHER_SAMPLE_SPACING_DEGREES", "0.25");
      vi.mocked(getCurrentWeather).mockResolvedValue({
        observedAt: new Date().toISOString(),
        temperature: 10,
        humidity: 50,
        windSpeed: 1,
        windDirection: 0,
        precipitation: 0,
      });

      await runWeatherIngestionJob();

      const calls = vi.mocked(getCurrentWeather).mock.calls;
      expect(calls.length).toBeLessThanOrEqual(150);
      expect(calls.length).toBeGreaterThan(0);

      const lats = calls.map(([lat]) => lat);
      const lons = calls.map(([, lon]) => lon);
      // The grid still spans (most of) the full extent -- no slicing.
      expect(Math.max(...lats) - Math.min(...lats)).toBeGreaterThan(7 * 0.8);
      expect(Math.max(...lons) - Math.min(...lons)).toBeGreaterThan(14 * 0.8);
    });
  });

  describe("resolveBoundedBbox policy", () => {
    it("throws on a malformed INGEST_BBOX shape", async () => {
      vi.stubEnv("INGEST_BBOX", "not,a,valid,bbox,shape");

      await expect(runFireIngestionJob()).rejects.toThrow(
        "INGEST_BBOX must be west,south,east,north"
      );
    });

    it("throws when the bbox exceeds the 30x20 degree bounded policy", async () => {
      vi.stubEnv("INGEST_BBOX", "-140,20,-100,50");

      await expect(runFireIngestionJob()).rejects.toThrow(
        "INGEST_BBOX is outside the bounded ingestion policy"
      );
    });
  });
});
