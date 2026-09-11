import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ water: vi.fn(), weather: vi.fn(), drought: vi.fn() }));
vi.mock("@/lib/server/services/parquet-trpc-readers", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/server/services/parquet-trpc-readers")>(),
  getParquetWaterGauges: mocks.water,
  getParquetWeatherObservations: mocks.weather,
  getParquetDrought: mocks.drought,
}));
import { contextPointProximity, getContextDrought, getContextWaterGauges, getContextWeatherForBbox, getContextWeatherForPoint } from "@/lib/server/services/parquet-context-readers";

const day = "2026-09-01";
const ready = (data: unknown[]) => ({ state: "ready", requestedDay: day, servedDay: day, truncated: false, data });
const weather = { latitude: 43.6, longitude: -116.2, observedDay: day, observedAt: `${day}T12:00:00Z`, temperatureC: 21, relativeHumidityPct: 33, windSpeedMs: 4, windDirectionDeg: null, precipitationMm: 0 };

beforeEach(() => vi.clearAllMocks());

describe("Parquet context adapters", () => {
  it.each(["water", "weather", "drought"] as const)("preserves direct %s governed absence for the assembler", async (source) => {
    const evidence = { reason: "Upstream history expired", upstreamResponse: "expired", recordedAt: `${day}T12:00:00Z`, runId: "absence" };
    mocks[source].mockResolvedValue({ state: "absent", requestedDay: day, servedDay: day, evidence });
    const read = source === "water" ? getContextWaterGauges : source === "weather" ? getContextWeatherForBbox : getContextDrought;
    await expect(read("-117,43,-116,44", day)).rejects.toMatchObject({ governedAbsence: { servedDay: day, evidence }, rungNotWritten: false });
  });

  it("preserves the publisher day when a late Pacific gauge instant falls on the next UTC day", async () => {
    const observedAt = "2026-09-10T06:45:00Z";
    mocks.water.mockResolvedValue(ready([{ siteNumber: "13334300", siteName: "Snake River McDuff", latitude: 45.94, longitude: -116.78, flowCfs: 14100, percentile: null, condition: null, observedDay: "2026-09-09", observedAt }]));
    expect(await getContextWaterGauges("-117.03,45.69,-116.53,46.19", "2026-09-09")).toMatchObject([{ observedDay: "2026-09-09", updatedAt: observedAt, flowCfs: 14100 }]);
    expect(mocks.water).toHaveBeenCalledWith(expect.objectContaining({ date: "2026-09-09" }));
  });
  it("reads a named day at the identity-bearing rung without inventing aggregate gauge identities", async () => {
    const gauge = { siteNumber: "13206000", siteName: "Boise River", latitude: 43.6, longitude: -116.2, flowCfs: null, percentile: null, condition: null, trend: null, observedAt: `${day}T12:00:00Z` };
    mocks.water.mockResolvedValue(ready([gauge]));
    const result = await getContextWaterGauges("-117,43,-116,44", day);
    expect(mocks.water).toHaveBeenCalledWith({ bbox: "-117,43,-116,44", date: day, mapZoom: 13 });
    expect(result).toEqual([{ siteNo: "13206000", siteName: "Boise River", lat: 43.6, lon: -116.2, flowCfs: null, percentile: null, condition: "unknown", trend: null, updatedAt: gauge.observedAt }]);
    mocks.water.mockResolvedValue(ready([gauge, { ...gauge, siteNumber: null }]));
    await expect(getContextWaterGauges("-117,43,-116,44", day)).rejects.toThrow("lacks identity");
  });

  it.each(["stable", "declining", "rising"])("withholds unverified %s gauge trends while retaining observed flow", async (trend) => {
    mocks.water.mockResolvedValue(ready([{ siteNumber: "13206000", siteName: "Boise River", latitude: 43.6, longitude: -116.2, flowCfs: 17.7, percentile: null, condition: "low", trend, observedAt: `${day}T12:00:00Z` }]));
    expect(await getContextWaterGauges("-117,43,-116,44", day)).toMatchObject([{ flowCfs: 17.7, percentile: null, condition: "unknown", trend: null }]);
  });

  it.each([null, -1, 101, Number.NaN, Number.POSITIVE_INFINITY, 12])("requires a valid percentile before retaining a gauge condition (%s)", async (percentile) => {
    mocks.water.mockResolvedValue(ready([{ siteNumber: "13206000", siteName: "Boise River", latitude: 43.6, longitude: -116.2, flowCfs: 17.7, percentile, condition: "low", trend: "stable", observedAt: `${day}T12:00:00Z` }]));
    expect(await getContextWaterGauges("-117,43,-116,44", day)).toMatchObject([{ flowCfs: 17.7, percentile: percentile === 12 ? 12 : null, condition: percentile === 12 ? "low" : "unknown", trend: null }]);
  });

  it("preserves weather units and nullable direction on the requested day", async () => {
    mocks.weather.mockResolvedValue(ready([weather]));
    expect(await getContextWeatherForBbox("-117,43,-116,44", day)).toEqual([{ lat: 43.6, lon: -116.2, observedDay: day, observedAt: weather.observedAt, temperature: 21, humidity: 33, windSpeed: 4, windDirection: null, precipitation: 0 }]);
    expect(mocks.weather).toHaveBeenCalledWith(expect.objectContaining({ date: day, mapZoom: 13 }));
  });

  it("bounds the nearest weather search and forwards cancellation", async () => {
    mocks.weather.mockResolvedValue(ready([{ ...weather, latitude: 43.8 }, weather]));
    const signal = new AbortController().signal;
    expect(await getContextWeatherForPoint(43.6, -116.2, signal)).toMatchObject({ lat: 43.6 });
    expect(mocks.weather).toHaveBeenCalledWith({ bbox: "-116.45,43.35,-115.95,43.85", date: undefined, mapZoom: 13, signal });
  });

  it("retains a named point-weather day and reports actual spatial and temporal distances", async () => {
    mocks.weather.mockResolvedValue(ready([weather]));
    expect(await getContextWeatherForPoint(43.6, -116.2, undefined, day)).toMatchObject({
      observedDay: day,
      proximity: { requestedDay: day, observedDay: day, distanceMeters: 0, dayOffset: 0, distanceDays: 0 },
    });
    expect(mocks.weather).toHaveBeenCalledWith(expect.objectContaining({ date: day, mapZoom: 13 }));
    expect(contextPointProximity(0, 0, { lat: 1, lon: 0, observedDay: "2026-08-26" }, day, "-1,-1,1,1")).toMatchObject({ dayOffset: -6, distanceDays: 6 });
    expect(contextPointProximity(0, 0, { lat: 1, lon: 0 }, day, "-1,-1,1,1").distanceMeters).toBeCloseTo(111194.9266, 3);
  });

  it("forwards cancellation through gauge and drought context reads", async () => {
    const signal = new AbortController().signal;
    mocks.water.mockResolvedValue(ready([]));
    mocks.drought.mockResolvedValue(ready([]));
    await getContextWaterGauges("-117,43,-116,44", day, signal);
    await getContextDrought("-117,43,-116,44", day, signal);
    expect(mocks.water).toHaveBeenCalledWith(expect.objectContaining({ signal }));
    expect(mocks.drought).toHaveBeenCalledWith(expect.objectContaining({ signal }));
  });

  it("keeps a drought release's date and category for point containment", async () => {
    const geometry = { type: "Polygon", coordinates: [[[-117,43],[-116,43],[-116,44],[-117,43]]] };
    mocks.drought.mockResolvedValue(ready([{ areaId: "d3", validDate: day, droughtCategory: 3, sourceUrl: "https://droughtmonitor.unl.edu/", geometry }]));
    expect(await getContextDrought("-117,43,-116,44", "2026-09-03")).toMatchObject({ availability: "published", observedAt: `${day}T00:00:00Z`, features: [{ geometry, properties: { DM: 3 } }] });
    expect(mocks.drought).toHaveBeenCalledWith({ bbox: "-117,43,-116,44", date: "2026-09-03", mapZoom: 13 });
  });

  it.each([
    { state: "not_generated", requestedDay: day, reason: "day_not_written" },
    { state: "upstream_unavailable", fault: { kind: "timeout", message: "deadline" } },
    { ...ready([weather]), truncated: true },
  ])("refuses incomplete evidence instead of returning an observed empty", async (read) => {
    mocks.weather.mockResolvedValue(read);
    await expect(getContextWeatherForPoint(43.6, -116.2)).rejects.toThrow();
  });

  it("preserves the client-closed error for an aborted point read", async () => {
    mocks.weather.mockResolvedValue({ state: "upstream_unavailable", fault: { kind: "aborted", message: "cancelled" } });
    await expect(getContextWeatherForPoint(43.6, -116.2)).rejects.toMatchObject({ code: "CLIENT_CLOSED_REQUEST" });
  });
});
