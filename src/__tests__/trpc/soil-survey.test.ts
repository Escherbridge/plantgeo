import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getParquetSoilSurvey: vi.fn() }));

vi.mock("@/lib/server/services/parquet-trpc-readers", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/server/services/parquet-trpc-readers")>();
  return { ...actual, getParquetSoilSurvey: mocks.getParquetSoilSurvey };
});

import type { Context } from "@/lib/server/trpc/init";
import { environmentalRouter } from "@/lib/server/trpc/routers/environmental";

const read = mocks.getParquetSoilSurvey;
const caller = environmentalRouter.createCaller({ db: {}, session: null } as unknown as Context);

/** `soil_survey_unavailable` in `planes/soil_survey.py` -- the frozen wire shape's raw envelope. */
const rawUnavailable = {
  type: "FeatureCollection" as const,
  features: [] as unknown[],
  availability: "unavailable" as const,
  reason: "soil_survey_release_not_admitted",
  truncated: false,
  revision: null,
  servedZoom: 13,
  requestedZoom: 13,
  temporalScope: { kind: "static_reference" as const, selectedDaySupported: false as const },
  spatialCoverage: null,
};

/**
 * `render_served_soil_survey` in `planes/soil_survey.py` -- a published answer. `declaredAreaCount`
 * (served) and `pendingAreaCount` are DISJOINT partitions of the release scope census
 * (`Release.complete_index` in `foundation/soil_survey/release.py`), never an overlapping pair.
 */
const rawPublished = {
  type: "FeatureCollection" as const,
  features: [] as unknown[],
  availability: "published" as const,
  reason: null,
  truncated: false,
  revision: "2026-09-01",
  releaseDay: "2026-09-01",
  capturedAt: "2026-09-01T00:00:00Z",
  servedZoom: 13,
  requestedZoom: 13,
  temporalScope: { kind: "static_reference" as const, selectedDaySupported: false as const },
  spatialCoverage: {
    declaredAreaCount: 220,
    pendingAreaCount: 44,
    viewportAreas: ["ID001", "ID002"],
  },
};

describe("SSURGO static reference route", () => {
  beforeEach(() => read.mockReset());

  it.each(["date", "day"])(
    "rejects %s instead of silently dropping a temporal request",
    async (parameter) => {
      const input = { bbox: "0,0,0.01,0.01", zoom: 13, [parameter]: "2020-01-01" };

      await expect(caller.getSoilSurvey(input)).rejects.toMatchObject({ code: "BAD_REQUEST" });
      expect(read).not.toHaveBeenCalled();
    }
  );

  it("adapts the frozen wire shape onto the ProxiedSoilSurveyCollection the panel and map read", async () => {
    read.mockResolvedValue(rawUnavailable);
    const input = { bbox: "0,0,0.01,0.01", zoom: 13 };

    const result = await caller.getSoilSurvey(input);

    // `getParquetSoilSurvey` is called with the router's own input, not a re-derivation --
    // `signal` rides through even when the caller never provided one.
    expect(read).toHaveBeenCalledWith({ bbox: "0,0,0.01,0.01", zoom: 13, signal: undefined });
    // Reason and availability pass straight through; `granularity`/`coverage`/
    // `unreadableGeometries`/`observedAt` are synthesized by `adaptSoilSurveyCollection` so
    // every existing consumer of `ProxiedSoilSurveyCollection` keeps reading the same shape.
    expect(result).toEqual({
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      reason: "soil_survey_release_not_admitted",
      truncated: false,
      unreadableGeometries: 0,
      observedAt: null,
      revision: null,
      granularity: "detail",
      coverage: { cells: 0, covered: 0, ingested: 0 },
    });
  });

  it("sums served and pending areas into coverage.cells rather than subtracting them", async () => {
    // Regression for the S4 review's coverage-math finding: `declaredAreaCount` (served) and
    // `pendingAreaCount` are disjoint partitions of the release scope, so `cells` is their SUM
    // and `covered` is `declaredAreaCount` alone -- never `declared - pending`, which would read
    // "176 of 220" for a release that is actually 220 of 264.
    read.mockResolvedValue(rawPublished);
    const input = { bbox: "0,0,0.01,0.01", zoom: 13 };

    const result = await caller.getSoilSurvey(input);

    expect(result.granularity).toBe("detail");
    expect(result.coverage).toEqual({ cells: 264, covered: 220, ingested: 2 });
  });

  it("reports the served zoom's granularity, so a below-z13 overview reads as averages", async () => {
    read.mockResolvedValue({
      ...rawPublished,
      servedZoom: 9,
      requestedZoom: 10,
      features: [
        {
          type: "Feature",
          geometry: null,
          properties: { aggregated: true, drainageClass: "Well drained", mapUnitCount: 3 },
        },
      ],
    });

    const result = await caller.getSoilSurvey({ bbox: "-117,43,-116,44", zoom: 10 });

    expect(result.granularity).toBe("regional-average");
  });

  it("keeps a zoom_in answer at detail even when the request was zoomed out", async () => {
    read.mockResolvedValue({ ...rawUnavailable, reason: "soil_survey_zoom_in", requestedZoom: 9 });

    const result = await caller.getSoilSurvey({ bbox: "-117,43,-116,44", zoom: 9 });

    expect(result.granularity).toBe("detail");
  });
});
