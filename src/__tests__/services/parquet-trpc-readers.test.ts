import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/services/parquet-plane-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/services/parquet-plane-client")>();
  return {
    ...actual,
    getParquetLayerDay: vi.fn(),
    getParquetLayerDayWindow: vi.fn(),
    getParquetLatestRelease: vi.fn(),
  };
});

import {
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import { ZoomTierResolutionError } from "@/lib/map/zoom-tiers";
import {
  getParquetLatestRelease,
  getParquetLayerDay,
  getParquetLayerDayWindow,
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";
import {
  getParquetDrought,
  getParquetFireDetections,
  getParquetMetricAtDate,
  getParquetVegetation,
  getParquetWaterGauges,
  getParquetWeatherObservations,
} from "@/lib/server/services/parquet-trpc-readers";

const mockedDay = vi.mocked(getParquetLayerDay);
const mockedWindow = vi.mocked(getParquetLayerDayWindow);
const mockedRelease = vi.mocked(getParquetLatestRelease);

const evidence = {
  reason: "source had no release",
  upstreamResponse: "200 {}",
  recordedAt: "2026-08-20T06:14:02Z",
  runId: "run-42",
};

function published(day: string, rows: readonly Record<string, unknown>[], servedDay = day) {
  return { state: "published" as const, requestedDay: day, servedDay, rows, truncated: false };
}

function waterRow() {
  return {
    site_number: "13042500",
    observed_at: "2026-08-20T18:15:00Z",
    observed_day: "2026-08-20",
    site_name: "Big Wood River",
    latitude: 43.52,
    longitude: -114.31,
    flow_cfs: 122,
    percentile: 61,
    condition: "normal",
    trend: "stable",
    source: "usgs-nwis",
    geometry_linked: true,
    data_available_at: "2026-08-20T18:30:00Z",
    ingested_at: "2026-08-20T18:31:00Z",
  };
}

function vegetationRow(day: string, value: number) {
  return {
    cell_id: null,
    grid_name: "sentinel-2-quarter-degree",
    metric_name: "ndvi",
    metric_unit: "1",
    observed_day: day,
    metric_value: value,
    observation_checksum: null,
    data_available_at: `${day}T18:30:00Z`,
    release_count: 1,
    allowed_client_exposure: true,
    cell_longitude: -114.25,
    cell_latitude: 43.5,
  };
}

beforeEach(() => {
  mockedDay.mockReset();
  mockedWindow.mockReset();
  mockedRelease.mockReset();
});

describe("Parquet tRPC state adapter", () => {
  it("maps a published water-gauge day to ready and resolves the shared zoom tier", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          ...waterRow(),
          observed_at: "2026-08-19T23:30:00Z",
          observed_day: "2026-08-19",
          flow_cfs: 90,
        },
      ]),
      published("2026-08-20", [
        { ...waterRow(), observed_at: "2026-08-20T17:15:00Z", flow_cfs: 100 },
        waterRow(),
      ]),
    ]);

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      mapZoom: 11.4,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "water-gauges",
      firstDay: "2026-08-19",
      lastDay: "2026-08-20",
      zoomTier: 9,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      data: [{ siteNumber: "13042500", flowCfs: 122 }],
    });
  });

  it("maps governed absence with all of its evidence", async () => {
    mockedDay.mockResolvedValue({
      state: "governed_absence",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      evidence,
    });

    await expect(
      getParquetWeatherObservations({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 13,
      })
    ).resolves.toEqual({
      state: "absent",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      evidence,
    });
  });

  it.each(["day_not_written", "lane_never_written"] as const)(
    "retains the %s reason under not_generated",
    async (reason) => {
      mockedDay.mockResolvedValue({ state: reason, requestedDay: "2026-08-20" });

      const result = await getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 5,
      });

      expect(result).toEqual({ state: "not_generated", requestedDay: "2026-08-20", reason });
    }
  );

  it.each([
    ["configuration", new UpstreamConfigurationError("missing URL")],
    ["http", new UpstreamHttpError(503)],
    ["payload", new UpstreamPayloadError("oversized")],
    ["timeout", new UpstreamTimeoutError("timed out")],
    ["contract", new ParquetPlaneContractError("wire drift")],
  ] as const)("makes a %s failure typed and visible", async (kind, error) => {
    mockedDay.mockRejectedValue(error);

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 9,
    });

    expect(result).toMatchObject({ state: "upstream_unavailable", fault: { kind } });
  });

  it("makes a fetch transport failure typed and visible", async () => {
    mockedDay.mockRejectedValue(new TypeError("fetch failed"));

    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-08-20", mapZoom: 9 })
    ).resolves.toMatchObject({ state: "upstream_unavailable", fault: { kind: "network" } });
  });

  it("does not swallow request, zoom, or programmer errors", async () => {
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "20 Aug", mapZoom: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-02-30", mapZoom: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-08-20", mapZoom: -1 })
    ).rejects.toBeInstanceOf(ZoomTierResolutionError);

    mockedDay.mockRejectedValue(new TypeError("programmer fault"));
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-08-20", mapZoom: 9 })
    ).rejects.toThrow("programmer fault");
  });

  it("fails a drifted lane row closed as an upstream contract fault", async () => {
    mockedDay.mockResolvedValue(published("2026-08-20", [{ site_number: "missing-the-rest" }]));

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 9,
    });

    expect(result).toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
  });
});

describe("lane day and release semantics", () => {
  it("retains today's not-generated state when yesterday has no fresh water row", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          ...waterRow(),
          observed_at: "2026-08-19T10:00:00Z",
          observed_day: "2026-08-19",
        },
      ]),
      { state: "day_not_written", requestedDay: "2026-08-20" },
    ]);

    await expect(
      getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-20T20:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-20",
      reason: "day_not_written",
    });
  });

  it("labels a fresh midnight-rollover water row with its publisher day", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          ...waterRow(),
          observed_at: "2026-08-19T23:30:00Z",
          observed_day: "2026-08-19",
        },
      ]),
      published("2026-08-20", []),
    ]);

    await expect(
      getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-20T02:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-19",
      data: [{ observedDay: "2026-08-19" }],
    });
  });

  it("refuses an empty live water answer when an earlier publisher day was truncated", async () => {
    mockedWindow.mockResolvedValue([
      { ...published("2026-08-19", []), truncated: true },
      published("2026-08-20", []),
    ]);

    await expect(
      getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-20T02:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "contract" },
    });
  });

  it("keeps the newest weather row per coordinate and drops stale live-day observations", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", []),
      published("2026-08-20", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T12:00:00Z",
          observed_day: "2026-08-20",
          external_id: "43.5000:-114.2500:2026-08-20T12:00:00Z",
          temperature_c: 20,
          relative_humidity_pct: 40,
          wind_speed_ms: 2,
          wind_direction_deg: 180,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "old",
          ingested_at: "2026-08-20T12:05:00Z",
        },
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T18:30:00Z",
          observed_day: "2026-08-20",
          external_id: "43.5000:-114.2500:2026-08-20T18:30:00Z",
          temperature_c: 24,
          relative_humidity_pct: 35,
          wind_speed_ms: 3,
          wind_direction_deg: 190,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "new",
          ingested_at: "2026-08-20T18:35:00Z",
        },
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T22:00:00Z",
          observed_day: "2026-08-20",
          external_id: "43.5000:-114.2500:2026-08-20T22:00:00Z",
          temperature_c: 99,
          relative_humidity_pct: 1,
          wind_speed_ms: 1,
          wind_direction_deg: 1,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "future",
          ingested_at: "2026-08-20T22:01:00Z",
        },
      ]),
    ]);

    const result = await getParquetWeatherObservations({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
    });

    expect(result).toMatchObject({
      state: "ready",
      data: [{ observedAt: "2026-08-20T18:30:00Z", temperatureC: 24 }],
    });
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("reads both publisher days for live weather before freshness filtering and deduplication", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-19T23:30:00Z",
          observed_day: "2026-08-19",
          external_id: "prior-new",
          temperature_c: 19,
          relative_humidity_pct: 45,
          wind_speed_ms: 2,
          wind_direction_deg: 180,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "prior-new",
          ingested_at: "2026-08-19T23:35:00Z",
        },
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-19T20:00:00Z",
          observed_day: "2026-08-19",
          external_id: "prior-stale",
          temperature_c: 17,
          relative_humidity_pct: 50,
          wind_speed_ms: 1,
          wind_direction_deg: 170,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "prior-stale",
          ingested_at: "2026-08-19T20:05:00Z",
        },
      ]),
      published("2026-08-20", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T03:00:00Z",
          observed_day: "2026-08-20",
          external_id: "future",
          temperature_c: 99,
          relative_humidity_pct: 1,
          wind_speed_ms: 1,
          wind_direction_deg: 1,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "future",
          ingested_at: "2026-08-20T03:01:00Z",
        },
      ]),
    ]);

    const result = await getParquetWeatherObservations({
      bbox: "-125,42,-111,49",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-20T01:00:00Z"),
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "weather-observations",
      firstDay: "2026-08-19",
      lastDay: "2026-08-20",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(mockedDay).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-19",
      data: [{ observedAt: "2026-08-19T23:30:00Z", temperatureC: 19 }],
    });
  });

  it("uses the release route and preserves drought's served release day", async () => {
    mockedRelease.mockResolvedValue(
      published(
        "2026-08-24",
        [
          {
            area_id: "area-1",
            valid_date: "2026-08-18",
            dm_category: 2,
            source_url: "https://droughtmonitor.unl.edu/",
            ingested_at: "2026-08-20T12:00:00Z",
            geom: JSON.stringify({
              type: "Polygon",
              coordinates: [
                [
                  [-120, 40],
                  [-119, 40],
                  [-119, 41],
                  [-120, 40],
                ],
              ],
            }),
          },
        ],
        "2026-08-18"
      )
    );

    const result = await getParquetDrought({
      bbox: "-125,42,-111,49",
      date: "2026-08-24",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-24T12:00:00Z"),
    });

    expect(mockedRelease).toHaveBeenCalledWith({
      layer: "drought",
      asOfDay: "2026-08-24",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-24",
      servedDay: "2026-08-18",
      data: [{ droughtCategory: 2, geometry: { type: "Polygon" } }],
    });
  });

  it("does not carry a drought release across a skipped historical release week", async () => {
    mockedRelease
      .mockResolvedValueOnce(published("2026-08-12", [], "2026-08-04"))
      .mockResolvedValueOnce(published("2026-08-24", [], "2026-08-18"));

    await expect(
      getParquetDrought({
        date: "2026-08-12",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-12",
      reason: "day_not_written",
    });
    expect(mockedRelease).toHaveBeenNthCalledWith(2, {
      layer: "drought",
      asOfDay: "2026-08-24",
      zoomTier: 13,
    });
  });

  it("does not carry an arbitrarily old newest drought release at the live edge", async () => {
    mockedRelease.mockResolvedValue(published("2026-08-24", [], "2026-08-09"));

    await expect(
      getParquetDrought({
        date: "2026-08-24",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-24",
      reason: "day_not_written",
    });
  });

  it("allows the newest drought release through the exact 14-day live-edge bound", async () => {
    mockedRelease.mockResolvedValue(published("2026-08-24", [], "2026-08-10"));

    await expect(
      getParquetDrought({
        date: "2026-08-24",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "ready",
      requestedDay: "2026-08-24",
      servedDay: "2026-08-10",
    });
  });

  it("rejects future drought and vegetation days before calling the Parquet plane", async () => {
    const nowMs = Date.parse("2026-08-24T12:00:00Z");

    await expect(
      getParquetDrought({ date: "2026-08-25", mapZoom: 13, nowMs })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetVegetation({
        bbox: "-125,42,-111,49",
        date: "2026-08-25",
        mapZoom: 13,
        nowMs,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedRelease).not.toHaveBeenCalled();
    expect(mockedWindow).not.toHaveBeenCalled();
  });

  it("reads vegetation through a trailing 30-day window and keeps the newest cell value", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-07-22", [vegetationRow("2026-07-22", 0.31)]),
      { state: "day_not_written", requestedDay: "2026-08-19" },
      published("2026-08-20", [vegetationRow("2026-08-20", 0.72)]),
    ]);

    const result = await getParquetVegetation({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 7,
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "vegetation",
      firstDay: "2026-07-22",
      lastDay: "2026-08-20",
      zoomTier: 5,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      data: {
        observations: [{ observedDay: "2026-08-20", metricValue: 0.72 }],
        days: [{ state: "ready" }, { state: "not_generated" }, { state: "ready" }],
      },
    });
  });

  it("fails the whole vegetation read closed when a row is not approved for client exposure", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-20", [
        { ...vegetationRow("2026-08-20", 0.72), allowed_client_exposure: false },
      ]),
    ]);

    await expect(
      getParquetVegetation({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 7,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "contract" },
    });
  });

  it("uses an exact one-day fire window for a named day", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-18", [
        {
          cell_longitude: -114.2,
          cell_latitude: 43.5,
          observed_day: "2026-08-18",
          detection_count: 4,
          frp_sum: 18.5,
          frp_observation_count: 3,
          high_confidence_detection_count: 2,
          newest_observed_at: "2026-08-18T22:15:00Z",
        },
      ]),
    ]);

    const result = await getParquetFireDetections({
      date: "2026-08-18",
      dayRange: 10,
      mapZoom: 3,
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "fire-detections",
      firstDay: "2026-08-18",
      lastDay: "2026-08-18",
      zoomTier: 0,
    });
    expect(result).toMatchObject({ state: "ready", data: { cells: [{ detectionCount: 4 }] } });
  });

  it("keeps generic metric reads unavailable until the frozen route can filter before its cap", async () => {
    await expect(getParquetMetricAtDate()).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "contract" },
    });
    expect(mockedDay).not.toHaveBeenCalled();
  });
});
