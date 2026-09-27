/**
 * The site brief's point readers, scheduler and cache (review M5, M6, M7, M8, M9). The reader
 * constants are asserted against the fixture agri's `tests/test_agent_site_brief_reader_constants.py`
 * asserts too, so the two readers cannot drift apart silently.
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getParquetLatestRelease: vi.fn(),
  getParquetLayerDayWindow: vi.fn(),
  getParquetFirePerimeters: vi.fn(),
  getParquetBurnSeverity: vi.fn(),
  getParquetDrought: vi.fn(),
  getSoilProperties: vi.fn(),
  enabledLayers: [{ layerSlug: "crop-cover" }] as { layerSlug: string }[],
}));

vi.mock("@/lib/server/services/parquet-plane-client", () => ({
  getParquetLatestRelease: mocks.getParquetLatestRelease,
  getParquetLayerDayWindow: mocks.getParquetLayerDayWindow,
}));
vi.mock("@/lib/server/services/parquet-trpc-readers", () => ({
  getParquetFirePerimeters: mocks.getParquetFirePerimeters,
  getParquetBurnSeverity: mocks.getParquetBurnSeverity,
  getParquetDrought: mocks.getParquetDrought,
}));
vi.mock("@/lib/server/services/soilgrids", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/soilgrids")>()),
  getSoilProperties: mocks.getSoilProperties,
}));
vi.mock("@/lib/region/region", () => ({ getRegion: () => ({ enabledLayers: mocks.enabledLayers }) }));

import {
  BURN_SEVERITY_CLASSES,
  burnSeverityOf,
  FIRE_DETECTION_RADIUS_METERS,
  FIRE_DETECTION_WINDOW_DAYS,
  readSiteBriefInputs,
  resetSiteBriefCacheForTests,
  SITE_BRIEF_CACHE_TTL_MS,
  SITE_BRIEF_CACHEABLE_UNAVAILABLE_REASONS,
  SITE_BRIEF_READ_CONCURRENCY,
  SITE_BRIEF_READ_DEADLINE_MS,
  SiteBriefDeadlineError,
  SiteBriefReadScheduler,
  siteBriefForRequest,
  siteBriefLandCoverSection,
  siteBriefWeatherSection,
  WEATHER_DAYS_BACK,
  WEATHER_RADIUS_METERS,
} from "@/lib/server/services/site-brief-readers";
import { readsFlagEnabled, siteBriefEnabled, type SiteBriefReason } from "@/lib/server/services/site-brief";
import {
  COVERAGE_COSINE_FLOOR,
  DEFAULT_SOIL_RADIUS_METERS,
  EARTH_RADIUS_METERS,
  MAX_SOIL_RADIUS_METERS,
  MIN_SOIL_RADIUS_METERS,
  outsideSoilReleaseCoverage,
  soilEstimateFromMapped,
  SOIL_CELL_DEGREES,
  SOIL_LATTICE_ENVELOPE,
  soilReadsEnabled,
} from "@/lib/server/services/soilgrids";

const FIXTURE_PATH = resolve(__dirname, "../../../services/agri-data-service/tests/fixtures/site_brief_reader_constants.json");
const TODAY = "2026-09-27";
const LAT = 43.6;
const LON = -116.2;
const MAPPED = {
  "0-5cm": { phh2o: 57, soc: 243, nitrogen: 190, bdod: 121, cec: 182, ocd: 380, clay: 189, sand: 371, silt: 440, cfvo: 98 },
  "5-15cm": { phh2o: 57, soc: 210, nitrogen: 160, bdod: 127, cec: 170, ocd: 340, clay: 195, sand: 380, silt: 425, cfvo: 102 },
  "15-30cm": { phh2o: 58, soc: 180, nitrogen: 130, bdod: 131, cec: 160, ocd: 300, clay: 201, sand: 390, silt: 409, cfvo: 106 },
};

const square = (west: number, south: number, size: number) => ({ type: "Polygon" as const,
  coordinates: [[[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]] });
const published = (requestedDay: string, rows: Record<string, unknown>[], truncated = false) =>
  ({ state: "published" as const, requestedDay, servedDay: requestedDay, rows, truncated });
const cropRow = (overrides: Record<string, unknown> = {}) => ({
  observed_year: 2025, release_day: "2026-01-30", aggregation_cell_m: 3000, cell_area_ha: 900,
  class_areas_json: JSON.stringify({ "176": 550.8, "24": 200 }),
  class_names_json: JSON.stringify({ "176": "Grassland/Pasture", "24": "Winter Wheat" }),
  geometry_wkb: square(LON - 0.02, LAT - 0.02, 0.04),
  ...overrides,
});

/** Every reader answers something readable; a test overrides only the one it is about. */
function stubReaders() {
  mocks.getSoilProperties.mockResolvedValue({ state: "available", properties: soilEstimateFromMapped(MAPPED, "soilgrids-v2.0/2020-06-02", 140) });
  mocks.getParquetFirePerimeters.mockResolvedValue({ state: "ready", requestedDay: TODAY, servedDay: TODAY, truncated: false, data: [] });
  mocks.getParquetBurnSeverity.mockResolvedValue({ state: "ready", requestedDay: TODAY, servedDay: TODAY, truncated: false, data: [] });
  mocks.getParquetDrought.mockResolvedValue({ state: "ready", requestedDay: TODAY, servedDay: "2026-09-22", truncated: false,
    data: [{ areaId: "a", validDate: "2026-09-22", droughtCategory: 1, sourceUrl: "https://x", ingestedAt: "x", geometry: square(-117, 43, 1) }] });
  mocks.getParquetLayerDayWindow.mockImplementation(async (request: { layer: string; firstDay: string; lastDay: string }) => {
    if (request.layer === "weather-observations") {
      return [
        { state: "day_not_written", requestedDay: "2026-09-26" },
        published(TODAY, [{ latitude: 43.7, longitude: -116.2, observed_at: "2026-09-27T15:00:00Z", temperature_c: 14.25, relative_humidity_pct: 37.5 }]),
      ];
    }
    return [published(TODAY, [{ cell_longitude: LON, cell_latitude: LAT, detection_count: 2 }])];
  });
  mocks.getParquetLatestRelease.mockResolvedValue(published("2026-01-30", [cropRow()]));
}

beforeEach(() => {
  vi.clearAllMocks();
  resetSiteBriefCacheForTests();
  mocks.enabledLayers = [{ layerSlug: "crop-cover" }];
  vi.stubEnv("SITE_BRIEF_ENABLED", "true");
  vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", "true");
  stubReaders();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

/* ------------------------------------------------------------------------------------------ */

describe("the shared reader-constant fixture (CONTRACT C5.1, review M9)", () => {
  expect(existsSync(FIXTURE_PATH), `reader-constant fixture missing at ${FIXTURE_PATH}`).toBe(true);
  const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf8"));

  it("is the version both languages read", () => {
    expect(fixture.fixture_version).toBe("site-brief-reader-constants/1");
  });

  it.each(fixture.flags.cases as [string | null, boolean][])("parses the flag value %j as %s in both flags", (value, expected) => {
    expect(readsFlagEnabled(value ?? undefined)).toBe(expected);
    for (const variable of [fixture.flags.soil_reads_variable, fixture.flags.site_brief_variable]) {
      if (value === null) vi.stubEnv(variable, undefined);
      else vi.stubEnv(variable, value);
    }
    expect(soilReadsEnabled()).toBe(expected);
    expect(siteBriefEnabled()).toBe(expected);
  });

  it("pins the brief's bounds, fire and weather constants", () => {
    expect(SITE_BRIEF_READ_DEADLINE_MS / 1000).toBe(fixture.brief_reads.deadline_seconds);
    expect(SITE_BRIEF_READ_CONCURRENCY).toBe(fixture.brief_reads.concurrency);
    expect(FIRE_DETECTION_RADIUS_METERS).toBe(fixture.fire.detection_radius_m);
    expect(FIRE_DETECTION_WINDOW_DAYS).toBe(fixture.fire.detection_window_days);
    expect(WEATHER_RADIUS_METERS).toBe(fixture.weather.radius_m);
    expect(WEATHER_DAYS_BACK).toBe(fixture.weather.days_back);
    expect(SITE_BRIEF_CACHE_TTL_MS / 1000).toBe(fixture.web_only.brief_cache_ttl_seconds);
    expect([...SITE_BRIEF_CACHEABLE_UNAVAILABLE_REASONS].sort()).toEqual(fixture.web_only.brief_cache_cacheable_unavailable_reasons);
  });

  it.each(fixture.fire.burn_severity_cases as [unknown, string | null][])("maps MTBS class %j to %s", (value, expected) => {
    expect(burnSeverityOf(value)).toBe(expected);
    expect(Object.keys(BURN_SEVERITY_CLASSES).length).toBe(6);
  });

  it("pins the soil reader constants", () => {
    const soil = fixture.soil;
    expect(DEFAULT_SOIL_RADIUS_METERS).toBe(soil.default_radius_m);
    expect(MIN_SOIL_RADIUS_METERS).toBe(soil.min_radius_m);
    expect(MAX_SOIL_RADIUS_METERS).toBe(soil.max_radius_m);
    expect(SOIL_CELL_DEGREES).toBe(soil.cell_degrees);
    expect(EARTH_RADIUS_METERS).toBe(soil.earth_radius_m);
    expect(COVERAGE_COSINE_FLOOR).toBe(soil.coverage_cosine_floor);
    expect(SOIL_LATTICE_ENVELOPE).toEqual(soil.envelope);
  });

  it.each(fixture.soil.coverage_cases as { name: string; longitude: number; latitude: number; radius_m: number; outside: boolean }[])(
    "applies the soil coverage rule to $name", (testCase) => {
      expect(outsideSoilReleaseCoverage(testCase.longitude, testCase.latitude, testCase.radius_m)).toBe(testCase.outside);
    });
});

/* ------------------------------------------------------------------------------------------ */

describe("SiteBriefReadScheduler (review M8)", () => {
  it("never holds more than two slots, and starts the next read as one settles", async () => {
    const scheduler = new SiteBriefReadScheduler(60_000, 2);
    const releases: (() => void)[] = [];
    let peak = 0;
    const reads = Array.from({ length: 5 }, () => scheduler.run(() => new Promise<string>((done) => {
      releases.push(() => done("ok"));
      peak = Math.max(peak, scheduler.inFlight);
    })));
    await Promise.resolve();
    expect(releases).toHaveLength(2);
    while (releases.length > 0) {
      releases.shift()?.();
      await new Promise((settle) => setTimeout(settle, 0));
    }
    expect(await Promise.all(reads)).toEqual(["ok", "ok", "ok", "ok", "ok"]);
    expect(peak).toBe(2);
    scheduler.dispose();
  });

  it("aborts every in-flight read's signal at the deadline and never starts a queued one", async () => {
    const scheduler = new SiteBriefReadScheduler(15, 2);
    const signals: AbortSignal[] = [];
    let started = 0;
    const reads = Array.from({ length: 3 }, () => scheduler.run((signal) => {
      started += 1;
      signals.push(signal);
      return new Promise<never>(() => {});
    }));
    const outcomes = await Promise.allSettled(reads);
    expect(started).toBe(2);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    for (const outcome of outcomes) {
      expect(outcome.status).toBe("rejected");
      expect((outcome as PromiseRejectedResult).reason).toBeInstanceOf(SiteBriefDeadlineError);
    }
    scheduler.dispose();
  });
});

/* ------------------------------------------------------------------------------------------ */

describe("section readers aligned with agri (review M9)", () => {
  it("passes the scheduler's signal to every brief read and uses the pinned circles", async () => {
    const scheduler = new SiteBriefReadScheduler();
    const inputs = await readSiteBriefInputs(LAT, LON, TODAY, scheduler);
    scheduler.dispose();
    for (const reader of [mocks.getParquetFirePerimeters, mocks.getParquetBurnSeverity, mocks.getParquetDrought,
      mocks.getParquetLayerDayWindow, mocks.getParquetLatestRelease]) {
      for (const [request] of reader.mock.calls) expect(request.signal).toBeInstanceOf(AbortSignal);
    }
    expect(mocks.getSoilProperties.mock.calls[0][2].signal).toBeInstanceOf(AbortSignal);
    const byLayer = Object.fromEntries(mocks.getParquetLayerDayWindow.mock.calls.map(([request]) => [request.layer, request]));
    expect(byLayer["fire-detections"]).toMatchObject({ firstDay: "2026-08-29", lastDay: TODAY, zoomTier: 13 });
    expect(byLayer["weather-observations"]).toMatchObject({ firstDay: "2026-09-26", lastDay: TODAY, zoomTier: 13 });
    const [west, , east] = byLayer["weather-observations"].bbox.split(",").map(Number);
    expect((east - west) / 2).toBeCloseTo(WEATHER_RADIUS_METERS / (111_320 * Math.cos((LAT * Math.PI) / 180)), 9);
    expect(mocks.getParquetLatestRelease.mock.calls[0][0]).toMatchObject({ layer: "crop-cover", zoomTier: 13, asOfDay: TODAY });
    expect(inputs).toMatchObject({
      fire: { state: "available", detections_last_30_days: 2, latest_fire_day: null },
      drought: { state: "available", usdm_class: "D1", week_of: "2026-09-22" },
      weather: { state: "available", temperature_tenths_c: 143, relative_humidity_pct: 38, observed_at: "2026-09-27T15:00:00Z" },
      land_cover: { state: "available", class_name: "Grassland/Pasture", class_code: 176, fraction_permille: 612 },
    });
  });

  it("counts detections inside the 10 km circle only, and refuses a capped day", async () => {
    mocks.getParquetLayerDayWindow.mockImplementation(async (request: { layer: string }) => request.layer === "fire-detections"
      ? [published(TODAY, [
        { cell_longitude: LON, cell_latitude: LAT, detection_count: 2 },
        // Inside the box's corner, outside the circle.
        { cell_longitude: LON + 0.12, cell_latitude: LAT + 0.085, detection_count: 50 },
      ])]
      : []);
    const scheduler = new SiteBriefReadScheduler();
    expect((await readSiteBriefInputs(LAT, LON, TODAY, scheduler)).fire).toMatchObject({ detections_last_30_days: 2 });
    mocks.getParquetLayerDayWindow.mockImplementation(async (request: { layer: string }) => request.layer === "fire-detections"
      ? [published(TODAY, [{ cell_longitude: LON, cell_latitude: LAT, detection_count: 2 }], true)] : []);
    expect((await readSiteBriefInputs(LAT, LON, TODAY, scheduler)).fire).toEqual({ state: "unavailable", reason: "read_failed" });
    scheduler.dispose();
  });

  it("grades a fire only by the same day's MTBS class, mapped from its code", async () => {
    mocks.getParquetFirePerimeters.mockResolvedValue({ state: "ready", requestedDay: TODAY, servedDay: TODAY, truncated: false,
      data: [{ featureId: "p", uniqueFireIdentifier: "x", snapshotDay: TODAY, observedDay: "2025-08-11", severity: "Low", geometry: square(-116.3, 43.5, 0.2) }] });
    mocks.getParquetBurnSeverity.mockResolvedValue({ state: "ready", requestedDay: TODAY, servedDay: TODAY, truncated: false, data: [
      { fireId: "m", ignitionDate: "2025-08-11", severityClass: 4, geometry: square(-116.3, 43.5, 0.2) },
      { fireId: "old", ignitionDate: "2020-07-01", severityClass: "2", geometry: square(-116.3, 43.5, 0.2) },
    ] });
    const scheduler = new SiteBriefReadScheduler();
    expect((await readSiteBriefInputs(LAT, LON, TODAY, scheduler)).fire).toMatchObject({ latest_fire_day: "2025-08-11", burn_severity: "high" });
    scheduler.dispose();
  });

  const weatherRead = (envelopes: unknown[]) => ({ status: "fulfilled" as const, value: envelopes as never });

  it("reads the newest published day, the nearest station within 50 km, ties to the newest reading", () => {
    const station = { latitude: 43.7, longitude: -116.2, temperature_c: 10, relative_humidity_pct: 40 };
    expect(siteBriefWeatherSection(LAT, LON, TODAY, weatherRead([
      published("2026-09-26", [{ ...station, observed_at: "2026-09-26T10:00:00Z" }]),
      { state: "day_not_written", requestedDay: TODAY },
    ]))).toMatchObject({ state: "available", observed_at: "2026-09-26T10:00:00Z" });
    expect(siteBriefWeatherSection(LAT, LON, TODAY, weatherRead([
      published(TODAY, [{ ...station, observed_at: "2026-09-27T09:00:00Z" }, { ...station, observed_at: "2026-09-27T11:00:00Z", temperature_c: 12 }]),
    ]))).toMatchObject({ state: "available", observed_at: "2026-09-27T11:00:00Z", temperature_tenths_c: 120 });
    // Today published but nobody within 50 km: stated, never filled from yesterday.
    expect(siteBriefWeatherSection(LAT, LON, TODAY, weatherRead([
      published("2026-09-26", [{ ...station, observed_at: "2026-09-26T10:00:00Z" }]),
      published(TODAY, [{ ...station, latitude: 45, observed_at: "2026-09-27T10:00:00Z" }]),
    ]))).toEqual({ state: "unavailable", reason: "no_observation_within_radius" });
    expect(siteBriefWeatherSection(LAT, LON, TODAY, weatherRead([
      { state: "lane_never_written", requestedDay: "2026-09-26" }, { state: "lane_never_written", requestedDay: TODAY },
    ]))).toEqual({ state: "unavailable", reason: "lane_never_written" });
  });

  it("refuses a weather reading that lacks a number instead of inventing one", () => {
    expect(siteBriefWeatherSection(LAT, LON, TODAY, weatherRead([
      published(TODAY, [{ latitude: 43.7, longitude: -116.2, observed_at: "2026-09-27T09:00:00Z", temperature_c: null, relative_humidity_pct: 40 }]),
    ]))).toEqual({ state: "unavailable", reason: "read_failed" });
  });

  it("refuses a dominant CDL class without a name rather than seeding a bare code", () => {
    const read = (row: Record<string, unknown>) => ({ status: "fulfilled" as const, value: published("2026-01-30", [row]) });
    expect(siteBriefLandCoverSection(LAT, LON, read(cropRow({ class_names_json: JSON.stringify({ "24": "Winter Wheat" }) }))))
      .toEqual({ state: "unavailable", reason: "read_failed" });
    expect(siteBriefLandCoverSection(LAT, LON, read(cropRow({ geometry_wkb: square(-100, 30, 0.04) }))))
      .toEqual({ state: "unavailable", reason: "outside_release_coverage" });
    expect(siteBriefLandCoverSection(LAT, LON, { status: "fulfilled", value: null }))
      .toEqual({ state: "unavailable", reason: "not_bound_in_region" });
  });
});

/* ------------------------------------------------------------------------------------------ */

const briefReaders = () => [mocks.getParquetFirePerimeters, mocks.getParquetBurnSeverity, mocks.getParquetDrought,
  mocks.getParquetLayerDayWindow, mocks.getParquetLatestRelease];

describe("siteBriefForRequest: flags, follow-ups and the cache", () => {
  it("with SITE_BRIEF_ENABLED off makes the soil read alone and builds no brief (review M7)", async () => {
    vi.stubEnv("SITE_BRIEF_ENABLED", undefined);
    const result = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(result.siteBrief).toBeNull();
    expect(result.soil.state).toBe("available");
    expect(mocks.getSoilProperties).toHaveBeenCalledTimes(1);
    for (const reader of briefReaders()) expect(reader).not.toHaveBeenCalled();
  });

  it("builds the brief on a base run and serves a follow-up from the cache with no read (review M6)", async () => {
    const base = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(base.siteBrief?.soil.state).toBe("available");
    vi.clearAllMocks();
    const followUp = await siteBriefForRequest(LAT + 0.001, LON, TODAY, false);
    expect(followUp.siteBrief).toEqual(base.siteBrief);
    expect(followUp.soil).toEqual(base.soil);
    expect(mocks.getSoilProperties).not.toHaveBeenCalled();
    for (const reader of briefReaders()) expect(reader).not.toHaveBeenCalled();
  });

  it("gives an uncached follow-up the one soil read and never the brief's other reads (review M6)", async () => {
    const followUp = await siteBriefForRequest(LAT, LON, TODAY, false);
    expect(followUp.siteBrief).toBeNull();
    expect(followUp.soil.state).toBe("available");
    expect(mocks.getSoilProperties).toHaveBeenCalledTimes(1);
    for (const reader of briefReaders()) expect(reader).not.toHaveBeenCalled();
  });

  it.each<[string, () => void, SiteBriefReason]>([
    ["a weather timeout", () => mocks.getParquetLayerDayWindow.mockImplementation(async (request: { layer: string }) => {
      if (request.layer === "weather-observations") throw new SiteBriefDeadlineError("late");
      return [];
    }), "timeout"],
    ["serving at capacity", () => mocks.getParquetDrought.mockResolvedValue({ state: "upstream_unavailable",
      fault: { kind: "upstream_error", message: "serving_at_capacity" } }), "serving_at_capacity"],
    ["a failed crop read", () => mocks.getParquetLatestRelease.mockRejectedValue(new Error("boom")), "read_failed"],
  ])("never caches a brief in which a section refused: %s (review M5)", async (_name, arrange, reason) => {
    arrange();
    const first = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(JSON.stringify(first.siteBrief)).toContain(`"reason":"${reason}"`);
    vi.clearAllMocks();
    stubReaders();
    await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(mocks.getSoilProperties).toHaveBeenCalledTimes(1);
  });

  it("caches a brief whose unavailable sections state plane facts, keyed by both flags (review M5)", async () => {
    mocks.getParquetLayerDayWindow.mockImplementation(async (request: { layer: string }) => request.layer === "weather-observations"
      ? [published(TODAY, [])] : []);
    const first = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(first.siteBrief?.weather).toEqual({ state: "unavailable", reason: "no_observation_within_radius" });
    vi.clearAllMocks();
    stubReaders();
    await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(mocks.getSoilProperties).not.toHaveBeenCalled();
    // Flipping the soil flag is a different key: the kill switch precedes the cache.
    vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", "false");
    await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(mocks.getSoilProperties).toHaveBeenCalledTimes(1);
  });

  it("caches a brief whose soil is reads_disabled, since the flag is in the key, and serves it to a follow-up", async () => {
    vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", undefined);
    mocks.getSoilProperties.mockResolvedValue({ state: "unavailable", reason: "reads_disabled" });
    const base = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(base.siteBrief?.soil).toEqual({ state: "unavailable", reason: "reads_disabled" });
    vi.clearAllMocks();
    const followUp = await siteBriefForRequest(LAT, LON, TODAY, false);
    expect(followUp.siteBrief).toEqual(base.siteBrief);
    expect(mocks.getSoilProperties).not.toHaveBeenCalled();
    // Re-enabling soil is a different key, so the stale reads_disabled section is never served.
    vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", "true");
    stubReaders();
    const reenabled = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(reenabled.siteBrief?.soil.state).toBe("available");
  });

  it("fails open to no brief when the inputs cannot be built, keeping the soil read", async () => {
    // A negative thickness-weighted sum is outside round-half-up's domain: buildSiteBrief throws.
    const corrupt = Object.fromEntries(Object.entries(MAPPED).map(([depth, values]) => [depth, { ...values, phh2o: -1 }])) as typeof MAPPED;
    const estimate = { ...soilEstimateFromMapped(MAPPED, "soilgrids-v2.0/2020-06-02", 140), mapped: corrupt };
    mocks.getSoilProperties.mockResolvedValue({ state: "available", properties: estimate });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const result = await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(result.siteBrief).toBeNull();
    expect(result.soil.state).toBe("available");
    expect(warn).toHaveBeenCalledWith("[site-brief] brief could not be built", "RangeError");
    warn.mockRestore();
  });

  it("expires a cached brief after the short TTL, so a weather reading is never frozen for the day (review M5)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(`${TODAY}T12:00:00Z`));
    await siteBriefForRequest(LAT, LON, TODAY, true);
    vi.clearAllMocks();
    stubReaders();
    vi.setSystemTime(new Date(Date.now() + SITE_BRIEF_CACHE_TTL_MS - 1_000));
    await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(mocks.getSoilProperties).not.toHaveBeenCalled();
    vi.setSystemTime(new Date(Date.now() + 2_000));
    await siteBriefForRequest(LAT, LON, TODAY, true);
    expect(mocks.getSoilProperties).toHaveBeenCalledTimes(1);
  });
});
