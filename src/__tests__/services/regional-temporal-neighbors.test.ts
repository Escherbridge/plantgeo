import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import type { ResolvedSliderCapabilities, ResolvedSliderLayerCapability } from "@/lib/server/services/environmental-read-model";
import type { ParquetFireDetectionCell, ParquetWaterGauge, ParquetWeatherObservation } from "@/lib/server/services/parquet-trpc-readers";

const mocks = vi.hoisted(() => ({ water: vi.fn(), weather: vi.fn(), fire: vi.fn() }));
vi.mock("@/lib/server/services/parquet-trpc-readers", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/server/services/parquet-trpc-readers")>(),
  getParquetWaterGauges: mocks.water,
  getParquetWeatherObservations: mocks.weather,
  getParquetFireDetections: mocks.fire,
}));
import { getRegionalTemporalNeighbours } from "@/lib/server/services/regional-temporal-neighbors";

const VIEWED_DAY = "2026-09-01";
const BBOX = "-2,-2,2,2";
const ready = <T>(day: string, data: readonly T[]) => ({ state: "ready" as const, requestedDay: day, servedDay: day, data, truncated: false });

function capability(layerName: string, overrides: Partial<ResolvedSliderLayerCapability> = {}): ResolvedSliderLayerCapability {
  return {
    layerName, temporalKind: layerName === "fire-detections" ? "event" : "daily_series",
    forecastHorizonDays: 0, forecastVariants: [], earliestObservedDate: "2026-01-01", latestObservedDate: "2026-09-10",
    coverageGaps: [], governedAbsenceRanges: [], thinRanges: [], describedFromDay: null, describedThroughDay: "2026-09-10",
    coverageAuthority: "availability", sourceCeilingDay: "2026-09-10", requiredRungs: [0, 5, 9, 13],
    earliestObservedDateRule: "warehouse_coverage", earliestRecordedObservationDate: "2026-01-01",
    earliestContinuousObservationDate: "2026-01-01", latestRecordedObservationDate: "2026-09-10",
    coverageGapsTruncated: false, coverageGapsDescribedFromDay: null, thinRangesTruncated: false, thinRangesDescribedFromDay: null,
    observedDayCount: 250, excludedObservedDayCount: 0, gapExcludedObservedDayCount: 0, densityExcludedObservedDayCount: 0,
    minimumDailyObservationCount: null, ...overrides,
  };
}

function payload(layers = [capability("water-gauges")]): ResolvedSliderCapabilities {
  return { serverCurrentDate: "2026-09-11", futureAxisDays: 30, streamsUnavailable: false, layers };
}

function support(layer: string, day: string): AggregateEnvelopeSupport {
  return {
    zoomTier: 13, supportKind: layer === "fire-detections" ? "aggregate_cell" : "raw_point",
    supportId: `${layer}:${day}:sample`, origin: "cell_center", aggregationMethod: layer === "fire-detections" ? "count" : "none",
    contributorCount: 1, provenance: { sourceLayer: layer, observedDay: day, newestObservedAt: `${day}T12:00:00Z`, attribution: "fixture" },
  };
}

function gauge(day: string, latitude = 0.1): ParquetWaterGauge {
  return {
    siteNumber: `site-${latitude}`, siteName: "Named gauge", latitude, longitude: 0, observedDay: day,
    observedAt: `${day}T12:00:00Z`, flowCfs: 41, percentile: null, condition: null, trend: null,
    source: "USGS", geometryLinked: true, dataAvailableAt: null, ingestedAt: `${day}T14:00:00Z`, support: support("water-gauges", day),
  };
}

function weather(day: string): ParquetWeatherObservation {
  return {
    latitude: 0, longitude: 0, observedAt: `${day}T12:00:00Z`, observedDay: day,
    externalId: "weather-point", temperatureC: 20, relativeHumidityPct: 50, windSpeedMs: 3, windDirectionDeg: 90,
    precipitationMm: 0, source: "Open-Meteo", featureId: null, ingestedAt: `${day}T14:00:00Z`, support: support("weather-observations", day),
  };
}

function fire(day: string): ParquetFireDetectionCell {
  return {
    latitude: 0, longitude: 0, observedDay: day, detectionCount: 3, frpSum: 7, frpObservationCount: 2,
    highConfidenceDetectionCount: 1, newestObservedAt: `${day}T12:00:00Z`, support: support("fire-detections", day),
  };
}

const input = () => ({ viewedLayers: [{ layer: "water", date: VIEWED_DAY }], capabilities: payload(), lat: 0, lon: 0, bbox: BBOX });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.water.mockImplementation(async ({ date }: { date: string }) => ready(date, [gauge(date)]));
  mocks.weather.mockImplementation(async ({ date }: { date: string }) => ready(date, [weather(date)]));
  mocks.fire.mockImplementation(async ({ date }: { date: string }) => ({
    ...ready(date, []), data: { firstDay: date, lastDay: date, cells: [fire(date)], days: [ready(date, [fire(date)])] },
  }));
});

describe("bounded regional temporal value neighbours", () => {
  it("skips gaps and governed absences, returning actual nearest values and signed distances", async () => {
    const signal = new AbortController().signal;
    const caps = capability("water-gauges", {
      coverageGaps: [{ from: "2026-08-31", to: "2026-08-31" }, { from: "2026-09-02", to: "2026-09-02" }],
      governedAbsenceRanges: [{ from: "2026-08-30", to: "2026-08-30" }, { from: "2026-09-03", to: "2026-09-03" }],
    });
    mocks.water.mockImplementation(async ({ date }: { date: string }) => ready(date, [gauge(date, 1), { ...gauge(date), observedAt: date === "2026-08-29" ? "2026-08-30T01:00:00Z" : `${date}T12:00:00Z` }]));
    const [result] = await getRegionalTemporalNeighbours({ ...input(), capabilities: payload([caps]), signal });
    expect(mocks.water.mock.calls.map(([request]) => request)).toEqual([
      { bbox: BBOX, date: "2026-08-29", mapZoom: 13, signal }, { bbox: BBOX, date: "2026-09-04", mapZoom: 13, signal },
    ]);
    expect(result.before).toMatchObject({ state: "observed", candidateDay: "2026-08-29", observation: { observedDay: "2026-08-29", dayOffset: -3, distanceDays: 3, searchBbox: BBOX, value: { siteNumber: "site-0.1", flowCfs: 41 } } });
    expect(result.before?.observation?.distanceMeters).toBeCloseTo(11119.492664, 4);
    expect(result.after).toMatchObject({ state: "observed", observation: { dayOffset: 3, distanceDays: 3 } });
  });

  it("deduplicates aliases and reads at most two days for each of three sources", async () => {
    const result = await getRegionalTemporalNeighbours({ ...input(), capabilities: payload([capability("fire-detections"), capability("water-gauges"), capability("weather-observations")]), viewedLayers: [
      { layer: "fire", date: VIEWED_DAY }, { layer: "fire-detections", date: VIEWED_DAY },
      { layer: "water", date: VIEWED_DAY }, { layer: "water-gauges", date: VIEWED_DAY },
      { layer: "weather", date: VIEWED_DAY }, { layer: "weather-observations", date: VIEWED_DAY },
    ] });
    expect(result).toHaveLength(3);
    expect(mocks.fire).toHaveBeenCalledTimes(2);
    expect(mocks.water).toHaveBeenCalledTimes(2);
    expect(mocks.weather).toHaveBeenCalledTimes(2);
    expect(result[0].before?.observation?.value).toMatchObject({ detectionCount: 3, observedDay: "2026-08-31" });
  });

  it("refuses conflicting alias dates before reading that source", async () => {
    const [result] = await getRegionalTemporalNeighbours({ ...input(), viewedLayers: [{ layer: "water", date: VIEWED_DAY }, { layer: "water-gauges", date: "2026-09-02" }] });
    expect(result).toMatchObject({ state: "refused", viewedDays: [VIEWED_DAY, "2026-09-02"], before: null, after: null });
    expect(mocks.water).not.toHaveBeenCalled();
  });

  it.each([
    { coverageAuthority: "census" as const }, { coverageAuthority: undefined },
    { describedThroughDay: undefined }, { describedThroughDay: null },
    { coverageGapsTruncated: true, coverageGapsDescribedFromDay: null },
    { sourceCeilingDay: "2026-02-30" },
  ])("refuses unproven availability bounds without a historical scan: %j", async (override) => {
    const [result] = await getRegionalTemporalNeighbours({ ...input(), capabilities: payload([capability("water-gauges", override)]) });
    expect(result.state).toBe("refused");
    expect(mocks.water).not.toHaveBeenCalled();
  });

  it("refuses absent or duplicate capabilities and global coverage failure without row reads", async () => {
    for (const capabilities of [null, payload([]), payload([capability("water-gauges"), capability("water-gauges")]), { ...payload(), streamsUnavailable: true }]) {
      expect((await getRegionalTemporalNeighbours({ ...input(), capabilities }))[0].state).toBe("refused");
    }
    expect(mocks.water).not.toHaveBeenCalled();
  });

  it("refuses an explicitly withheld capability even if a contradictory layer row is present", async () => {
    const capabilities = { ...payload(), withheldParquetCapabilities: [{ layerName: "water-gauges", reason: "availability_stale" }] };
    expect((await getRegionalTemporalNeighbours({ ...input(), capabilities }))[0]).toMatchObject({ state: "refused", reason: "The source capability is explicitly withheld." });
    expect(mocks.water).not.toHaveBeenCalled();
  });

  it("clips candidate days to described bounds and the source ceiling", async () => {
    const caps = capability("water-gauges", { describedFromDay: VIEWED_DAY, sourceCeilingDay: "2026-09-02" });
    const [result] = await getRegionalTemporalNeighbours({ ...input(), capabilities: payload([caps]) });
    expect(result.before?.state).toBe("no_published_candidate");
    expect(result.after?.candidateDay).toBe("2026-09-02");
    expect(mocks.water).toHaveBeenCalledTimes(1);
  });

  it("includes the 180th day but never probes a published day 181 days away", async () => {
    const caps = capability("water-gauges", { latestObservedDate: "2026-12-31", describedThroughDay: "2026-12-31", sourceCeilingDay: "2026-12-31", coverageGaps: [
      { from: "2026-01-03", to: "2026-06-30" }, { from: "2026-07-02", to: "2026-12-28" },
    ] });
    const [result] = await getRegionalTemporalNeighbours({ ...input(), viewedLayers: [{ layer: "water", date: "2026-07-01" }], capabilities: { ...payload([caps]), serverCurrentDate: "2026-12-31" } });
    expect(result.before?.observation).toMatchObject({ observedDay: "2026-01-02", dayOffset: -180, distanceDays: 180 });
    expect(result.after?.state).toBe("no_published_candidate");
    expect(mocks.water).toHaveBeenCalledTimes(1);
  });

  it("names an empty nearest globally published day and never scans another local day", async () => {
    mocks.water.mockImplementation(async ({ date }: { date: string }) => ready(date, []));
    const [result] = await getRegionalTemporalNeighbours(input());
    expect(result.before).toMatchObject({ state: "no_local_observation", candidateDay: "2026-08-31", observation: null });
    expect(result.before?.reason).toContain("No local observation on nearest globally published day");
    expect(result.after?.state).toBe("no_local_observation");
    expect(mocks.water).toHaveBeenCalledTimes(2);
  });

  it("retains terminal absence, missing partition, upstream refusal and truncation", async () => {
    const absence = { state: "absent", requestedDay: "2026-08-31", servedDay: "2026-08-31", evidence: { reason: "provider empty", upstreamResponse: "no rows", recordedAt: "2026-08-31T01:00:00Z", runId: "receipt-1" } };
    mocks.water.mockResolvedValueOnce(absence).mockResolvedValueOnce({ state: "not_generated", requestedDay: "2026-09-02", reason: "day_not_written" });
    const [terminal] = await getRegionalTemporalNeighbours(input());
    expect(terminal.before).toMatchObject({ state: "absent", read: absence });
    expect(terminal.after).toMatchObject({ state: "not_generated", read: { reason: "day_not_written" } });
    mocks.water.mockResolvedValueOnce({ ...ready("2026-08-31", [gauge("2026-08-31")]), truncated: true }).mockResolvedValueOnce({ state: "upstream_unavailable", fault: { kind: "http", message: "reader refused", status: 503 } });
    const [refused] = await getRegionalTemporalNeighbours(input());
    expect(refused.before).toMatchObject({ state: "truncated", observation: null, read: { truncated: true, rowCount: 1 } });
    expect(refused.after).toMatchObject({ state: "upstream_unavailable", read: { fault: { status: 503 } } });
  });

  it.each(["day", "bbox", "identity", "rung"])("refuses invalid %s rows instead of inventing nearest evidence", async (defect) => {
    const row = gauge("2026-08-31");
    if (defect === "day") row.observedDay = VIEWED_DAY;
    if (defect === "bbox") row.latitude = 3;
    if (defect === "identity") row.siteNumber = null;
    if (defect === "rung") row.support.zoomTier = 9;
    mocks.water.mockResolvedValueOnce(ready("2026-08-31", [row]));
    expect((await getRegionalTemporalNeighbours(input()))[0].before).toMatchObject({ state: "refused", observation: null });
  });

  it("refuses an exact-day envelope mismatch and a fire window spanning other days", async () => {
    mocks.water.mockResolvedValueOnce(ready(VIEWED_DAY, [gauge(VIEWED_DAY)]));
    expect((await getRegionalTemporalNeighbours(input()))[0].before).toMatchObject({ state: "refused", observation: null });
    mocks.fire.mockResolvedValueOnce({ ...ready("2026-08-31", []), data: { firstDay: "2026-08-30", lastDay: "2026-08-31", cells: [], days: [ready("2026-08-30", []), ready("2026-08-31", [])] } });
    const [result] = await getRegionalTemporalNeighbours({ ...input(), viewedLayers: [{ layer: "fire", date: VIEWED_DAY }], capabilities: payload([capability("fire-detections")]) });
    expect(result.before).toMatchObject({ state: "upstream_unavailable", read: { fault: { kind: "contract" } } });
  });

  it("keeps release, static and non-Parquet layers explicitly unsupported", async () => {
    const result = await getRegionalTemporalNeighbours({ ...input(), viewedLayers: ["drought", "drought-areas", "burn-severity", "watersheds", "community"].map((layer) => ({ layer, date: VIEWED_DAY })) });
    expect(result).toHaveLength(4);
    expect(result.every((row) => row.state === "unsupported" && row.before === null && row.after === null)).toBe(true);
    expect(mocks.water).not.toHaveBeenCalled();
    expect(mocks.weather).not.toHaveBeenCalled();
    expect(mocks.fire).not.toHaveBeenCalled();
  });

  it("propagates aborts and never starts the other candidate after cancellation", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(getRegionalTemporalNeighbours({ ...input(), signal: controller.signal })).rejects.toMatchObject({ code: "CLIENT_CLOSED_REQUEST" });
    expect(mocks.water).not.toHaveBeenCalled();
    mocks.water.mockResolvedValueOnce({ state: "upstream_unavailable", fault: { kind: "aborted", message: "cancelled" } });
    await expect(getRegionalTemporalNeighbours(input())).rejects.toMatchObject({ code: "CLIENT_CLOSED_REQUEST" });
    expect(mocks.water).toHaveBeenCalledTimes(1);
  });
});
