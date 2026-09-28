import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return {
    ...actual,
    providerUrl: vi.fn(() => new URL("http://parquet.test")),
    fetchBoundedJson: vi.fn(),
  };
});

import { fetchBoundedJson, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { getParquetSoilSurvey } from "@/lib/server/services/parquet-trpc-readers/soil-survey";
import {
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";

const fetch = vi.mocked(fetchBoundedJson);

/**
 * `soil_survey_unavailable` in `planes/soil_survey.py` -- the FROZEN wire shape's unavailable
 * envelope. No `requestedDay`/`servedDay`/`observedAt`/`granularity`/`coverage` fields exist on
 * the wire at all; those are a `ProxiedSoilSurveyCollection` concept the router's adapter
 * (`environmental.ts#adaptSoilSurveyCollection`) synthesizes, one layer up from this reader.
 */
const unavailable = {
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

describe("admitted-release SSURGO bridge", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads a static reference with only zoom and bbox on the bounded request", async () => {
    fetch.mockResolvedValue(unavailable);

    const result = await getParquetSoilSurvey({ bbox: "-117,43,-116,44", zoom: 11.5 });

    expect(result.availability).toBe("unavailable");
    const url = fetch.mock.calls[0]?.[0] as URL;
    expect(url.pathname).toBe("/api/v1/soil-survey/query");
    expect(url.searchParams.get("zoom")).toBe("11");
    expect([...url.searchParams.keys()].sort()).toEqual(["bbox", "zoom"]);
    expect(result.temporalScope).toEqual({ kind: "static_reference", selectedDaySupported: false });
    expect(fetch.mock.calls[0]?.[2]).toMatchObject({ maxBytes: 16 * 1024 * 1024, timeoutMs: 15_000 });
  });

  it.each(["date", "day"])(
    "refuses an unsupported %s before making any upstream request",
    async (parameter) => {
      const input = { bbox: "0,0,1,1", [parameter]: "2020-01-01" };

      await expect(getParquetSoilSurvey(input)).rejects.toBeInstanceOf(ParquetPlaneRequestError);
      expect(fetch).not.toHaveBeenCalled();
    }
  );

  it("keeps release evidence attached only to a published answer", async () => {
    fetch.mockResolvedValue(unavailable);

    const result = await getParquetSoilSurvey({ bbox: "0,0,1,1" });

    expect(result).toMatchObject({
      availability: "unavailable",
      revision: null,
      spatialCoverage: null,
    });
    expect(result).not.toHaveProperty("releaseDay");
    expect(result).not.toHaveProperty("capturedAt");
  });

  it("carries the admitted release's revision, capture day and touched areas on a published answer", async () => {
    fetch.mockResolvedValue({
      ...unavailable,
      availability: "published",
      reason: null,
      revision: "a".repeat(64),
      releaseDay: "2025-08-27",
      capturedAt: "2026-09-13T12:00:00+00:00",
      spatialCoverage: { viewportAreas: ["ID001"], declaredAreaCount: 220, pendingAreaCount: 12 },
    });

    const result = await getParquetSoilSurvey({ bbox: "0,0,1,1" });

    expect(result).toMatchObject({
      availability: "published",
      revision: "a".repeat(64),
      releaseDay: "2025-08-27",
      capturedAt: "2026-09-13T12:00:00+00:00",
      spatialCoverage: { viewportAreas: ["ID001"], declaredAreaCount: 220, pendingAreaCount: 12 },
    });
  });

  it("rejects a published response that omits its release evidence", async () => {
    fetch.mockResolvedValue({ ...unavailable, availability: "published", reason: null });

    await expect(getParquetSoilSurvey({ bbox: "0,0,1,1" })).rejects.toBeInstanceOf(
      ParquetPlaneContractError
    );
  });

  it("preserves transport failure instead of inventing missing soil", async () => {
    const error = new UpstreamTimeoutError("SSURGO timeout");
    fetch.mockRejectedValue(error);

    await expect(getParquetSoilSurvey({ bbox: "0,0,1,1" })).rejects.toBe(error);
  });

  // Region-guard case: `is_layer_bound` refuses before the admission pin or the zoom gate are
  // ever checked (`interface/http/soil_survey.py`'s gate order). The reader must pass this
  // through unchanged rather than treating an unbound region as a contract violation.
  it("passes through an unbound-region refusal without inventing release evidence", async () => {
    fetch.mockResolvedValue({ ...unavailable, reason: "no_source_bound_in_region" });

    const result = await getParquetSoilSurvey({ bbox: "0,0,1,1" });

    expect(result).toMatchObject({
      availability: "unavailable",
      reason: "no_source_bound_in_region",
      spatialCoverage: null,
    });
  });

  // `soil_survey_zoom_in` case: below the native z13 rung the route answers unavailable rather
  // than degrading to a coarser candidate -- there is no coarser one to fall back to (plan Q1).
  it("passes through the zoom-in refusal below the native rung", async () => {
    fetch.mockResolvedValue({ ...unavailable, reason: "soil_survey_zoom_in", requestedZoom: 9 });

    const result = await getParquetSoilSurvey({ bbox: "0,0,1,1", zoom: 9 });

    expect(result).toMatchObject({
      availability: "unavailable",
      reason: "soil_survey_zoom_in",
      servedZoom: 13,
      requestedZoom: 9,
    });
  });

  // Regression for the S4 review's degenerate-geometry finding: Q4 ("repair else quarantine
  // label and serve always", `planes/soil_survey.py`) means an `invalid_unrepaired` row's
  // original, unrepaired WKB can reach this port with a Z-bearing position or a ring under 4
  // points -- `_filter_point_candidates_by_ring` names "a Z/M dimension" explicitly as a variant
  // DuckDB's own decoder can hand back. A single such row must not fail `safeParse` for the
  // whole viewport, since that turns one quarantine-labelled row into a 503 for every OTHER
  // valid row in the response.
  it("parses a viewport containing one degenerate, Z-bearing, quarantine-labelled row", async () => {
    fetch.mockResolvedValue({
      ...unavailable,
      availability: "published",
      reason: null,
      revision: "a".repeat(64),
      releaseDay: "2025-08-27",
      capturedAt: "2026-09-13T12:00:00+00:00",
      spatialCoverage: { viewportAreas: ["ID001"], declaredAreaCount: 220, pendingAreaCount: 44 },
      features: [
        {
          type: "Feature",
          id: "mup-degenerate-1",
          geometry: {
            type: "Polygon",
            // A 3-point, Z-bearing ring: below the old min(4)/2-numbers-only schema, this alone
            // used to 503 the whole viewport rather than serve the other rows around it.
            coordinates: [
              [
                [-116.5, 43.5, 0],
                [-116.4, 43.5, 0],
                [-116.45, 43.55, 0],
              ],
            ],
          },
          properties: {
            mupolygonkey: "mup-degenerate-1",
            mukey: "mu-1",
            muname: null,
            soilSeries: null,
            drainageClass: null,
            hydric: null,
            landCapabilityClass: null,
            areaSymbol: null,
            surveyAreaVintage: "2020-01-01",
            geometryQuality: "invalid_unrepaired",
            geometryRepresentation: "native",
            source: "usda-sda",
            releaseSha256: "a".repeat(64),
          },
        },
      ],
    });

    const result = await getParquetSoilSurvey({ bbox: "0,0,1,1" });

    expect(result.availability).toBe("published");
    expect(result.features).toHaveLength(1);
    expect(result.features[0]?.properties.geometryQuality).toBe("invalid_unrepaired");
  });
});
