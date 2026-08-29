import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getMetricAtDate: vi.fn(),
  getPublishedClimateField: vi.fn(),
  getPublishedSoilField: vi.fn(),
  getParquetClimateField: vi.fn(),
  getParquetSoilField: vi.fn(),
  metricQuery: vi.fn(),
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  CLIMATE_FIELD_MAX_CELLS: 512,
  SOIL_FIELD_MAX_CELLS: 512,
  getMetricAtDate: mocks.getMetricAtDate,
  getPublishedClimateField: mocks.getPublishedClimateField,
  getPublishedGroundwaterWells: vi.fn(),
  getPublishedSoilField: mocks.getPublishedSoilField,
}));

vi.mock("@/lib/server/services/parquet-trpc-readers", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/server/services/parquet-trpc-readers")>();
  return {
    ...actual,
    getParquetDrought: vi.fn(),
    getParquetClimateField: mocks.getParquetClimateField,
    getParquetSoilField: mocks.getParquetSoilField,
    getParquetVegetation: vi.fn(),
    getParquetWaterGauges: vi.fn(),
  };
});

vi.mock("@/lib/server/services/parquet-slider-capabilities", () => ({
  getParquetSliderCapabilities: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  getVanillaTrpcClient: () => ({
    environmental: { getMetricAtDate: { query: mocks.metricQuery } },
  }),
}));

import type { Context } from "@/lib/server/trpc/init";
import { UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { ParquetPlaneContractError } from "@/lib/server/services/parquet-plane-client";
import { environmentalRouter } from "@/lib/server/trpc/routers/environmental";
import { fetchMetricAtDate } from "@/stores/useMetricAtDate";
import type { MetricAtDateCollection, MetricAtDateInput } from "@/types/time-slider";

const input: MetricAtDateInput = {
  metric: "perimeter-acres",
  date: "2026-08-05",
  variant: "observed",
  bbox: "-117,43,-116,44",
};

const collection: MetricAtDateCollection = {
  type: "FeatureCollection",
  features: [],
  availability: "published",
  reason: null,
};

const climateCollection = {
  type: "FeatureCollection" as const,
  features: [],
  availability: "unavailable" as const,
  reason: "not_published" as const,
};

const caller = environmentalRouter.createCaller({
  db: {},
  session: null,
} as unknown as Context);

beforeEach(() => {
  mocks.getMetricAtDate.mockReset();
  mocks.getMetricAtDate.mockResolvedValue(collection);
  mocks.getPublishedClimateField.mockReset();
  mocks.getPublishedClimateField.mockResolvedValue(climateCollection);
  mocks.getPublishedSoilField.mockReset();
  mocks.getParquetClimateField.mockReset();
  mocks.getParquetClimateField.mockResolvedValue({
    state: "not_generated",
    requestedDay: "2026-08-05",
    reason: "day_not_written",
  });
  mocks.getParquetSoilField.mockReset();
  mocks.getParquetSoilField.mockResolvedValue({
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    reason: "not_published",
  });
  mocks.metricQuery.mockReset();
});

describe("environmental reader ownership dispatch", () => {
  it("routes the soil field exclusively through the Parquet reader", async () => {
    await caller.getSoilField({
      bbox: "-117,43,-116,44",
      measure: "moisture",
      depth: "surface",
      date: "2026-08-05",
      zoom: 13,
    });

    expect(mocks.getParquetSoilField).toHaveBeenCalledWith("-117,43,-116,44", {
      date: "2026-08-05",
      measure: "moisture",
      depth: "surface",
      zoom: 13,
    });
  });

  it.each([
    ["timeout", new UpstreamTimeoutError("private Parquet timeout")],
    ["contract", new ParquetPlaneContractError("soil row contract drift")],
  ])("fails a soil %s closed without consulting PostgreSQL", async (_kind, failure) => {
    mocks.getParquetSoilField.mockRejectedValue(failure);

    await expect(
      caller.getSoilField({
        bbox: "-117,43,-116,44",
        measure: "moisture",
        depth: "surface",
        date: "2026-08-05",
        zoom: 13,
      })
    ).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });

    expect(mocks.getPublishedSoilField).not.toHaveBeenCalled();
  });

  it.each([
    "air-temperature",
    "dew-point",
    "precipitation",
    "relative-humidity",
    "shortwave-radiation",
    "wind-speed",
    "soil-wetness-surface",
    "soil-wetness-root-zone",
    "soil-wetness-profile",
  ])(
    "routes migrated climate signal %s exclusively through Parquet",
    async (signal) => {
      await caller.getClimateField({
        bbox: "-117,43,-116,44",
        date: "2026-08-05",
        signal,
      });

      expect(mocks.getParquetClimateField).toHaveBeenCalledWith({
        bbox: "-117,43,-116,44",
        date: "2026-08-05",
        signal,
        variant: "mean",
      });
      expect(mocks.getPublishedClimateField).not.toHaveBeenCalled();
    }
  );

  it("routes an omitted climate signal to the default air-temperature Parquet reader", async () => {
    await caller.getClimateField({ bbox: "-117,43,-116,44" });

    expect(mocks.getParquetClimateField).toHaveBeenCalledWith({
      bbox: "-117,43,-116,44",
      date: undefined,
      signal: "air-temperature",
      variant: "mean",
    });
    expect(mocks.getPublishedClimateField).not.toHaveBeenCalled();
  });

  it("fails a migrated climate signal closed without consulting PostgreSQL", async () => {
    mocks.getParquetClimateField.mockResolvedValue({
      state: "upstream_unavailable",
      fault: { kind: "http", message: "private Parquet API unavailable" },
    });

    await expect(
      caller.getClimateField({
        bbox: "-117,43,-116,44",
        date: "2026-08-05",
        signal: "precipitation",
      })
    ).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });
    expect(mocks.getPublishedClimateField).not.toHaveBeenCalled();
  });

  it("rejects an unknown climate signal before either reader is invoked", async () => {
    await expect(
      caller.getClimateField({
        bbox: "-117,43,-116,44",
        signal: "future-climate-signal" as never,
      })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });

    expect(mocks.getParquetClimateField).not.toHaveBeenCalled();
    expect(mocks.getPublishedClimateField).not.toHaveBeenCalled();
  });

  it.each(["perimeter-acres", "percent-contained"] as const)(
    "keeps the PostgreSQL-owned %s metric on its authoritative reader",
    async (metric) => {
      const request = { ...input, metric };

      await expect(caller.getMetricAtDate(request)).resolves.toEqual({
        state: "ready",
        requestedDay: input.date,
        servedDay: input.date,
        data: collection,
        truncated: false,
      });
      expect(mocks.getMetricAtDate).toHaveBeenCalledWith(request);
    }
  );

  it.each([
    "streamflow-cfs",
    "temperature",
    "humidity",
    "precipitation",
    "wind-speed",
    "wind-direction",
    "fire-radiative-power",
    "fire-brightness",
    "drought-category",
    "unregistered-signal",
  ])("rejects unsupported metric %s at the public input boundary", async (metric) => {
    const invalidInput = { ...input, metric } as unknown as MetricAtDateInput;

    await expect(caller.getMetricAtDate(invalidInput)).rejects.toMatchObject({
      code: "BAD_REQUEST",
    });
    expect(mocks.getMetricAtDate).not.toHaveBeenCalled();
  });

  it("propagates a selected PostgreSQL owner's failure without another read", async () => {
    const failure = new Error("PostgreSQL unavailable");
    mocks.getMetricAtDate.mockRejectedValue(failure);

    await expect(caller.getMetricAtDate(input)).rejects.toThrow("PostgreSQL unavailable");
    expect(mocks.getMetricAtDate).toHaveBeenCalledTimes(1);
  });
});

describe("metric-at-date client transport", () => {
  it("unwraps a PostgreSQL-owned collection from the shared ready envelope", async () => {
    mocks.metricQuery.mockResolvedValue({
      state: "ready",
      requestedDay: input.date,
      servedDay: input.date,
      data: collection,
      truncated: false,
    });

    await expect(fetchMetricAtDate(input)).resolves.toBe(collection);
  });
});
