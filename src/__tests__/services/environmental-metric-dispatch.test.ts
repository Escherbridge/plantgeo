import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getMetricAtDate: vi.fn(),
  getParquetMetricAtDate: vi.fn(),
  metricQuery: vi.fn(),
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getMetricAtDate: mocks.getMetricAtDate,
  getPublishedClimateField: vi.fn(),
  getPublishedGroundwaterWells: vi.fn(),
  getPublishedSoilField: vi.fn(),
}));

vi.mock("@/lib/server/services/parquet-trpc-readers", () => ({
  getParquetDrought: vi.fn(),
  getParquetMetricAtDate: mocks.getParquetMetricAtDate,
  getParquetVegetation: vi.fn(),
  getParquetWaterGauges: vi.fn(),
}));

vi.mock("@/lib/server/services/parquet-slider-capabilities", () => ({
  getParquetSliderCapabilities: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  getVanillaTrpcClient: () => ({
    environmental: { getMetricAtDate: { query: mocks.metricQuery } },
  }),
}));

import type { Context } from "@/lib/server/trpc/init";
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

const refusal = {
  state: "upstream_unavailable" as const,
  fault: {
    kind: "contract" as const,
    message: "The frozen Parquet route cannot filter signal_name before its row budget",
  },
};

const caller = environmentalRouter.createCaller({
  db: {},
  session: null,
} as unknown as Context);

beforeEach(() => {
  mocks.getMetricAtDate.mockReset();
  mocks.getMetricAtDate.mockResolvedValue(collection);
  mocks.getParquetMetricAtDate.mockReset();
  mocks.getParquetMetricAtDate.mockResolvedValue(refusal);
  mocks.metricQuery.mockReset();
});

describe("environmental.getMetricAtDate ownership dispatch", () => {
  it.each(["perimeter-acres", "percent-contained"])(
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
      expect(mocks.getParquetMetricAtDate).not.toHaveBeenCalled();
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
  ])("fails the Parquet-owned %s metric closed without consulting PostgreSQL", async (metric) => {
    await expect(caller.getMetricAtDate({ ...input, metric })).resolves.toEqual(refusal);
    expect(mocks.getParquetMetricAtDate).toHaveBeenCalledTimes(1);
    expect(mocks.getMetricAtDate).not.toHaveBeenCalled();
  });

  it("does not fall back to Parquet when the selected PostgreSQL owner fails", async () => {
    const failure = new Error("PostgreSQL unavailable");
    mocks.getMetricAtDate.mockRejectedValue(failure);

    await expect(caller.getMetricAtDate(input)).rejects.toThrow("PostgreSQL unavailable");
    expect(mocks.getParquetMetricAtDate).not.toHaveBeenCalled();
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
