import { beforeEach, describe, expect, it, vi } from "vitest";

import { ndviCellKeyForPoint, ndviForecastSeriesKeyForPoint } from "@/lib/forecast/series-key";

/**
 * providerUrl and fetchBoundedJson are the only seams stubbed: providerUrl so the
 * not-configured path is reachable outside production, fetchBoundedJson so no test
 * touches the network. Everything else (error classes, mapping) runs for real.
 */
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return {
    ...actual,
    providerUrl: vi.fn(actual.providerUrl),
    fetchBoundedJson: vi.fn(),
  };
});

import {
  fetchBoundedJson,
  providerUrl,
  UpstreamConfigurationError,
} from "@/lib/server/http/bounded-upstream";
import {
  ForecastContractError,
  getPublishedForecastSeries,
  toPublishedSeries,
  type UpstreamForecastPage,
} from "@/lib/server/services/agri-forecasts";

const mockedProviderUrl = vi.mocked(providerUrl);
const mockedFetch = vi.mocked(fetchBoundedJson);

const RECEIPT_A = "0d4b0f34-0000-4000-8000-00000000000a";
const RECEIPT_B = "0d4b0f34-0000-4000-8000-00000000000b";

function servingRecord(overrides: {
  validTime: string;
  horizonStep: number;
  receiptId?: string;
  publicationId?: string;
  issueTime?: string;
  pointValue?: number;
  forecastMethod?: "sql_linear" | "ml";
}) {
  return {
    publication: {
      publication_id: overrides.publicationId ?? "6f6f9a20-0000-4000-8000-000000000001",
      publication_key: "pub-2026-08-08",
      published_at: "2026-08-08T06:00:00Z",
      receipt_checksum: "sha256:receipt",
      forecast_receipt_id: overrides.receiptId ?? RECEIPT_A,
    },
    series: {
      series_key: "ndvi-daily:sentinel2-ndvi-0p25deg:43.1250:-113.6250",
      entity_type: "grid_cell",
      entity_key: "43.1250:-113.6250",
      metric_name: "ndvi",
      metric_unit: "ndvi_index",
    },
    lineage: { forecast_method: overrides.forecastMethod ?? "ml" },
    point: {
      issue_time: overrides.issueTime ?? "2026-08-08T00:00:00Z",
      valid_time: overrides.validTime,
      horizon_step: overrides.horizonStep,
      point_value: overrides.pointValue ?? 0.42,
      p10_value: 0.35,
      p50_value: 0.42,
      p90_value: 0.51,
    },
  };
}

function page(data: ReturnType<typeof servingRecord>[], hasMore = false): UpstreamForecastPage {
  return { data, limit: 250, offset: 0, hasMore };
}

describe("ndvi series-key derivation", () => {
  // Expectations are hard-coded literals on purpose: they pin the Python naming
  // contract itself, so a drifted prefix or grid name fails here instead of
  // rendering as the deliberate "nothing published yet" empty state.
  it("snaps an interior point to its 0.25° cell centre", () => {
    expect(ndviCellKeyForPoint(43.2, -113.7)).toBe("43.1250:-113.6250");
    expect(ndviForecastSeriesKeyForPoint(43.2, -113.7)).toBe(
      "ndvi-daily:sentinel2-ndvi-0p25deg:43.1250:-113.6250"
    );
  });

  it("assigns a point on a cell edge to the cell it opens (floor semantics)", () => {
    expect(ndviCellKeyForPoint(43.25, -113.75)).toBe("43.3750:-113.6250");
  });

  it("renders negative coordinates with the contract's four decimals", () => {
    expect(ndviCellKeyForPoint(-1.0, -0.1)).toBe("-0.8750:-0.1250");
  });
});

describe("toPublishedSeries", () => {
  it("draws only the latest-issue receipt when a page spans two runs", () => {
    // R1 (sql_linear, issued 08-07) covers 08-08..08-10; R2 (ml, issued 08-08)
    // covers 08-09..08-11. The page arrives interleaved in server order.
    const result = toPublishedSeries(
      "series-a",
      page([
        servingRecord({ validTime: "2026-08-08T00:00:00Z", horizonStep: 1, receiptId: RECEIPT_A, issueTime: "2026-08-07T00:00:00Z", forecastMethod: "sql_linear" }),
        servingRecord({ validTime: "2026-08-09T00:00:00Z", horizonStep: 2, receiptId: RECEIPT_A, issueTime: "2026-08-07T00:00:00Z", forecastMethod: "sql_linear" }),
        servingRecord({ validTime: "2026-08-09T00:00:00Z", horizonStep: 1, receiptId: RECEIPT_B, issueTime: "2026-08-08T00:00:00Z" }),
        servingRecord({ validTime: "2026-08-10T00:00:00Z", horizonStep: 3, receiptId: RECEIPT_A, issueTime: "2026-08-07T00:00:00Z", forecastMethod: "sql_linear" }),
        servingRecord({ validTime: "2026-08-10T00:00:00Z", horizonStep: 2, receiptId: RECEIPT_B, issueTime: "2026-08-08T00:00:00Z" }),
        servingRecord({ validTime: "2026-08-11T00:00:00Z", horizonStep: 3, receiptId: RECEIPT_B, issueTime: "2026-08-08T00:00:00Z" }),
      ])
    );
    expect(result.points.map((point) => point.validTime)).toEqual([
      "2026-08-09T00:00:00Z",
      "2026-08-10T00:00:00Z",
      "2026-08-11T00:00:00Z",
    ]);
    expect(result.issuedAt).toBe("2026-08-08T00:00:00Z");
    expect(result.forecastMethod).toBe("ml");
    expect(result.staleReceiptPointsDropped).toBe(3);
  });

  it("never draws one receipt twice when two publications carry it", () => {
    // forecast_publication_item is keyed (publication_id, forecast_receipt_id),
    // so the same receipt can arrive under two publications. Receipt-only
    // grouping would merge both copies and double-draw every point.
    const PUB_2 = "6f6f9a20-0000-4000-8000-000000000002";
    const result = toPublishedSeries(
      "series-a",
      page([
        servingRecord({ validTime: "2026-08-09T00:00:00Z", horizonStep: 1 }),
        servingRecord({ validTime: "2026-08-09T00:00:00Z", horizonStep: 1, publicationId: PUB_2 }),
        servingRecord({ validTime: "2026-08-10T00:00:00Z", horizonStep: 2 }),
        servingRecord({ validTime: "2026-08-10T00:00:00Z", horizonStep: 2, publicationId: PUB_2 }),
      ])
    );
    expect(result.points.map((point) => point.validTime)).toEqual([
      "2026-08-09T00:00:00Z",
      "2026-08-10T00:00:00Z",
    ]);
    expect(result.staleReceiptPointsDropped).toBe(2);
  });

  it("keeps a single receipt's points in server order without re-sorting", () => {
    const result = toPublishedSeries(
      "series-a",
      page([
        servingRecord({ validTime: "2026-08-09T00:00:00Z", horizonStep: 1 }),
        servingRecord({ validTime: "2026-08-10T00:00:00Z", horizonStep: 2 }),
      ])
    );
    expect(result.availability).toBe("published");
    expect(result.points.map((point) => point.horizonStep)).toEqual([1, 2]);
    expect(result.staleReceiptPointsDropped).toBe(0);
    expect(result.metricName).toBe("ndvi");
  });

  it("keeps an empty page published, with no identity to invent", () => {
    const result = toPublishedSeries("series-a", page([]));
    expect(result).toMatchObject({
      availability: "published",
      reason: null,
      points: [],
      metricName: null,
      issuedAt: null,
      staleReceiptPointsDropped: 0,
      hasMore: false,
    });
  });
});

describe("getPublishedForecastSeries", () => {
  beforeEach(() => {
    mockedProviderUrl.mockReset();
    mockedFetch.mockReset();
  });

  it("reports unavailable instead of throwing when no base URL is configured", async () => {
    mockedProviderUrl.mockImplementation(() => {
      throw new UpstreamConfigurationError("AGRI_DATA_SERVICE_URL is not set");
    });
    const result = await getPublishedForecastSeries({
      seriesKey: "series-a",
      limit: 250,
      offset: 0,
    });
    expect(result.availability).toBe("unavailable");
    expect(result.reason).toBe("forecast_service_not_configured");
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("queries the serving route with the bounded contract parameters", async () => {
    mockedProviderUrl.mockReturnValue(new URL("http://agri.internal:8000"));
    mockedFetch.mockResolvedValue(
      page([servingRecord({ validTime: "2026-08-09T00:00:00Z", horizonStep: 1 })])
    );

    const result = await getPublishedForecastSeries({
      seriesKey: "ndvi-daily:sentinel2-ndvi-0p25deg:43.1250:-113.6250",
      limit: 100,
      offset: 0,
    });

    const requestedUrl = mockedFetch.mock.calls[0][0] as URL;
    expect(requestedUrl.pathname).toBe("/forecasts/");
    expect(requestedUrl.searchParams.get("series_key")).toBe(
      "ndvi-daily:sentinel2-ndvi-0p25deg:43.1250:-113.6250"
    );
    expect(requestedUrl.searchParams.get("spatial")).toBe("none");
    expect(requestedUrl.searchParams.get("limit")).toBe("100");
    expect(result.points).toHaveLength(1);
    expect(result.points[0]).toMatchObject({ p10: 0.35, p50: 0.42, p90: 0.51 });
  });

  it("rejects a payload that drifts from the published contract", async () => {
    mockedProviderUrl.mockReturnValue(new URL("http://agri.internal:8000"));
    mockedFetch.mockResolvedValue({ rows: [] });
    await expect(
      getPublishedForecastSeries({ seriesKey: "series-a", limit: 250, offset: 0 })
    ).rejects.toBeInstanceOf(ForecastContractError);
  });
});
