/**
 * Point reads behind the server-built site brief (CONTRACT C5.1 reader-side normalisation), the
 * bounded scheduler they run under, and the brief's short-lived cache. The reader constants are
 * pinned by CONTRACT C5.1 and checked against the shared fixture
 * `services/agri-data-service/tests/fixtures/site_brief_reader_constants.json`, which agri's
 * `agent/graph.py` readers are checked against too. See soil/AGENTS.md §site-brief-readers.
 */
import { z } from "zod";
import { getRegion } from "@/lib/region/region";
import { UpstreamHttpError, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { decodePublishedGeometry } from "@/lib/server/services/land-context/geometry/boundary-geometry-adapter";
import { getParquetLatestRelease, getParquetLayerDayWindow } from "@/lib/server/services/parquet-plane-client";
import type { ParquetPlaneEnvelope } from "@/lib/server/services/parquet-envelope";
import {
  getParquetBurnSeverity,
  getParquetDrought,
  getParquetFirePerimeters,
  type ParquetBurnScar,
  type ParquetDroughtArea,
  type ParquetFirePerimeter,
  type ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";
import { getSoilProperties, haversineMeters, soilReadsEnabled, type SoilRead } from "@/lib/server/services/soilgrids";
import {
  buildSiteBrief,
  siteBriefEnabled,
  type BurnSeverity,
  type SiteBrief,
  type SiteBriefInputs,
  type SiteBriefReason,
  type UsdmClass,
} from "@/lib/server/services/site-brief";

/* ---------------------------------------------------------------------------
 * Reader constants (CONTRACT C5.1; shared fixture site_brief_reader_constants.json)
 * ------------------------------------------------------------------------- */

/** One deadline for every brief read of a request; the base run never waits longer (CONTRACT C2). */
export const SITE_BRIEF_READ_DEADLINE_MS = 3_000;
/** At most this many brief reads hold a slot on the 3-slot serving plane at once (review M8). */
export const SITE_BRIEF_READ_CONCURRENCY = 2;
/** "Nearby" for satellite detections: a circle to each detection cell's coordinate. */
export const FIRE_DETECTION_RADIUS_METERS = 10_000;
export const FIRE_DETECTION_WINDOW_DAYS = 30;
/** The nearest weather station within this circle, on today (else yesterday), UTC days. */
export const WEATHER_RADIUS_METERS = 50_000;
export const WEATHER_DAYS_BACK = 1;
/** MTBS thematic classes 2-4 and their names; classes 1, 5 and 6 carry no severity. */
export const BURN_SEVERITY_CLASSES: Readonly<Record<string, BurnSeverity>> = {
  "2": "low", "3": "moderate", "4": "high", low: "low", moderate: "moderate", high: "high",
};
/** Half-width of the box a containment read asks for: a fire or drought polygon must contain the point. */
const CONTAINMENT_HALF_WIDTH_DEGREES = 0.01;
/** Wider than half a 3 km crop-cover cell, so the cell containing the point is always returned. */
const CROP_HALF_WIDTH_DEGREES = 0.03;
const METERS_PER_DEGREE_LATITUDE = 110_574;
const METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR = 111_320;
const COSINE_FLOOR = 0.01;
const SITE_BRIEF_FIRE_SOURCE = "fire-perimeters + burn-severity (MTBS)";
const CROP_LAYER = "crop-cover";
const MILLISECONDS_PER_DAY = 86_400_000;

/** Map an MTBS class code or name to its severity, or null for a class that carries none. */
export function burnSeverityOf(value: unknown): BurnSeverity | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  return BURN_SEVERITY_CLASSES[String(value).trim().toLowerCase()] ?? null;
}

/* ---------------------------------------------------------------------------
 * Bounded scheduler (review M8)
 * ------------------------------------------------------------------------- */

export class SiteBriefDeadlineError extends Error {}

/** A brief read that refused with a known reason rather than failing. */
export class SiteBriefRefusal extends Error {
  constructor(readonly reason: SiteBriefReason) {
    super(`site brief read refused: ${reason}`);
  }
}

/**
 * Semaphore plus one shared deadline: at most `concurrency` reads in flight, each handed the
 * scheduler's AbortSignal, which aborts at the deadline so the transport lets go of its slot. A read
 * not started by the deadline never starts. A slot is released when the underlying read settles.
 */
export class SiteBriefReadScheduler {
  private readonly controller = new AbortController();
  private readonly timer: ReturnType<typeof setTimeout>;
  private active = 0;
  private readonly waiting: { start: () => void; refuse: (error: Error) => void }[] = [];

  constructor(deadlineMs = SITE_BRIEF_READ_DEADLINE_MS, private readonly concurrency = SITE_BRIEF_READ_CONCURRENCY) {
    this.timer = setTimeout(() => this.expire(), deadlineMs);
  }

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  /** Reads currently holding a slot (test seam for the concurrency cap). */
  get inFlight(): number {
    return this.active;
  }

  async run<T>(read: (signal: AbortSignal) => Promise<T>): Promise<T> {
    await this.acquire();
    const underlying = (async () => read(this.signal))();
    underlying.then(() => this.release(), () => this.release());
    const signal = this.signal;
    return Promise.race([
      underlying,
      new Promise<never>((_, reject) => {
        const refuse = () => reject(new SiteBriefDeadlineError("site brief read deadline"));
        if (signal.aborted) refuse();
        else signal.addEventListener("abort", refuse, { once: true });
      }),
    ]);
  }

  /** Stop the deadline timer once every read has settled. */
  dispose(): void {
    clearTimeout(this.timer);
  }

  private acquire(): Promise<void> {
    if (this.signal.aborted) return Promise.reject(new SiteBriefDeadlineError("site brief read deadline"));
    if (this.active < this.concurrency) {
      this.active += 1;
      return Promise.resolve();
    }
    return new Promise((start, refuse) => this.waiting.push({ start, refuse }));
  }

  private release(): void {
    this.active -= 1;
    const next = this.waiting.shift();
    if (next === undefined) return;
    this.active += 1;
    next.start();
  }

  private expire(): void {
    this.controller.abort(new SiteBriefDeadlineError("site brief read deadline"));
    for (const waiter of this.waiting.splice(0)) waiter.refuse(new SiteBriefDeadlineError("site brief read deadline"));
  }
}

/* ---------------------------------------------------------------------------
 * Reason mapping and geometry
 * ------------------------------------------------------------------------- */

/** Map a thrown read onto the one reason vocabulary (CONTRACT C5.6). */
export function siteBriefFailureReason(error: unknown): SiteBriefReason {
  if (error instanceof SiteBriefRefusal) return error.reason;
  if (error instanceof SiteBriefDeadlineError || error instanceof UpstreamTimeoutError) return "timeout";
  if (error instanceof UpstreamHttpError && error.bodyText?.includes('"serving_at_capacity"')) return "serving_at_capacity";
  // Duck-typed: `ParquetContextReadError` marks an unwritten detail rung.
  if ((error as { rungNotWritten?: unknown } | null)?.rungNotWritten === true) return "not_published";
  return "read_failed";
}

/** Map a Parquet reader's refusal state onto the reason vocabulary. */
function readerRefusalReason(result: ParquetReaderResult<unknown>): SiteBriefReason {
  if (result.state === "not_generated") return result.reason === "lane_never_written" ? "lane_never_written" : "not_published";
  if (result.state === "upstream_unavailable") {
    if (result.fault.kind === "timeout" || result.fault.kind === "aborted") return "timeout";
    if (result.fault.message.includes("serving_at_capacity")) return "serving_at_capacity";
  }
  return "read_failed";
}

/** Ray casting (even-odd rule) over a polygon's rings; holes subtract. */
function ringsContainPoint(rings: readonly (readonly (readonly number[])[])[], lat: number, lon: number): boolean {
  let inside = false;
  for (const ring of rings) {
    for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
      const [currentLon, currentLat] = ring[index];
      const [previousLon, previousLat] = ring[previous];
      if ((currentLat > lat) !== (previousLat > lat)
        && lon < ((previousLon - currentLon) * (lat - currentLat)) / (previousLat - currentLat) + currentLon) {
        inside = !inside;
      }
    }
  }
  return inside;
}

/** Whether a Polygon or MultiPolygon contains the point. */
export function geometryContainsPoint(geometry: GeoJSON.Geometry | null | undefined, lat: number, lon: number): boolean {
  if (!geometry) return false;
  if (geometry.type === "Polygon") return ringsContainPoint(geometry.coordinates, lat, lon);
  if (geometry.type === "MultiPolygon") return geometry.coordinates.some((polygon) => ringsContainPoint(polygon, lat, lon));
  return false;
}

function pointBbox(lat: number, lon: number, halfWidth: number): string {
  return [
    Math.max(-180, lon - halfWidth), Math.max(-90, lat - halfWidth),
    Math.min(180, lon + halfWidth), Math.min(90, lat + halfWidth),
  ].join(",");
}

/** The box around a circle of `radiusMeters`, with the C5.1 cosine floor. */
function radiusBbox(lat: number, lon: number, radiusMeters: number): string {
  const halfLatitude = radiusMeters / METERS_PER_DEGREE_LATITUDE;
  const halfLongitude = radiusMeters / (METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * Math.max(Math.cos((lat * Math.PI) / 180), COSINE_FLOOR));
  return [
    Math.max(-180, lon - halfLongitude), Math.max(-90, lat - halfLatitude),
    Math.min(180, lon + halfLongitude), Math.min(90, lat + halfLatitude),
  ].join(",");
}

function shiftCalendarDay(day: string, days: number): string {
  const instant = Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
  return new Date(instant + days * MILLISECONDS_PER_DAY).toISOString().slice(0, 10);
}

/** Round a reader float half up to an integer (C5.1 normalisation). */
function halfUp(value: number): number {
  return Math.floor(value + 0.5);
}

/** Printable ASCII only (CONTRACT C5.1 string normalisation). */
function printableAscii(value: string): string {
  return value.replace(/[^\x20-\x7E]+/g, "").trim();
}

/** Degrees x 1e5 as integers, rounded half away from zero. */
export function siteBriefPoint(lat: number, lon: number): SiteBriefInputs["point"] {
  const scaled = (value: number) => Math.sign(value) * Math.floor(Math.abs(value) * 1e5 + 0.5);
  return { longitude_e5: scaled(lon), latitude_e5: scaled(lat) };
}

/* ---------------------------------------------------------------------------
 * Section normalisers (pure; tested per language)
 * ------------------------------------------------------------------------- */

/** Soil section from the one soil read. */
export function siteBriefSoilSection(read: PromiseSettledResult<SoilRead>): SiteBriefInputs["soil"] {
  if (read.status === "rejected") return { state: "unavailable", reason: siteBriefFailureReason(read.reason) };
  const soil = read.value;
  if (soil.state === "unavailable") {
    return { state: "unavailable", reason: soil.reason, ...(soil.radiusM !== undefined ? { radius_m: soil.radiusM } : {}) };
  }
  return {
    state: "available",
    release_id: soil.properties.releaseId,
    distance_m: soil.properties.distanceM,
    mapped: soil.properties.mapped,
  };
}

/**
 * Fire section: the newest mapped fire containing the point (WFIGS perimeter or MTBS burn), its
 * MTBS severity when the SAME fire carries exactly one, and satellite detections nearby in 30 days.
 * Any failed read makes the whole section unavailable: an unread source is never a zero.
 */
export function siteBriefFireSection(
  lat: number,
  lon: number,
  builtOn: string,
  perimeters: PromiseSettledResult<ParquetReaderResult<readonly ParquetFirePerimeter[]>>,
  burns: PromiseSettledResult<ParquetReaderResult<readonly ParquetBurnScar[]>>,
  detections: PromiseSettledResult<number>,
): SiteBriefInputs["fire"] {
  for (const read of [perimeters, burns, detections]) {
    if (read.status === "rejected") return { state: "unavailable", reason: siteBriefFailureReason(read.reason) };
  }
  const perimeterResult = (perimeters as PromiseFulfilledResult<ParquetReaderResult<readonly ParquetFirePerimeter[]>>).value;
  const burnResult = (burns as PromiseFulfilledResult<ParquetReaderResult<readonly ParquetBurnScar[]>>).value;
  for (const result of [perimeterResult, burnResult]) {
    if (result.state === "upstream_unavailable" || result.state === "not_generated") {
      return { state: "unavailable", reason: readerRefusalReason(result) };
    }
  }
  const perimeterDays = perimeterResult.state === "ready"
    ? perimeterResult.data.filter((row) => row.observedDay !== null && row.observedDay <= builtOn
      && geometryContainsPoint(row.geometry as GeoJSON.Geometry, lat, lon)).map((row) => row.observedDay as string)
    : [];
  const containingBurns = burnResult.state === "ready"
    ? burnResult.data.filter((scar) => /^\d{4}-\d{2}-\d{2}/.test(scar.ignitionDate) && scar.ignitionDate.slice(0, 10) <= builtOn
      && geometryContainsPoint(scar.geometry as GeoJSON.Geometry, lat, lon))
    : [];
  const latestFireDay = [...perimeterDays, ...containingBurns.map((scar) => scar.ignitionDate.slice(0, 10))].sort().at(-1) ?? null;
  const severities = new Set(containingBurns
    .filter((scar) => scar.ignitionDate.slice(0, 10) === latestFireDay)
    .map((scar) => burnSeverityOf(scar.severityClass))
    .filter((severity): severity is BurnSeverity => severity !== null));
  return {
    state: "available",
    latest_fire_day: latestFireDay,
    burn_severity: severities.size === 1 ? [...severities][0] : null,
    detections_last_30_days: (detections as PromiseFulfilledResult<number>).value,
    source: SITE_BRIEF_FIRE_SOURCE,
  };
}

/** Drought section: the highest USDM class of the newest release's polygons containing the point, or `none`. */
export function siteBriefDroughtSection(
  lat: number,
  lon: number,
  read: PromiseSettledResult<ParquetReaderResult<readonly ParquetDroughtArea[]>>,
): SiteBriefInputs["drought"] {
  if (read.status === "rejected") return { state: "unavailable", reason: siteBriefFailureReason(read.reason) };
  const result = read.value;
  if (result.state === "absent") return { state: "unavailable", reason: "not_published" };
  if (result.state !== "ready") return { state: "unavailable", reason: readerRefusalReason(result) };
  // A truncated read cannot prove which polygon contains the point.
  if (result.truncated) return { state: "unavailable", reason: "read_failed" };
  let level: number | null = null;
  for (const area of result.data) {
    if (geometryContainsPoint(area.geometry as GeoJSON.Geometry, lat, lon) && (level === null || area.droughtCategory > level)) {
      level = area.droughtCategory;
    }
  }
  const usdmClass: UsdmClass = level === null ? "none" : (`D${level}` as UsdmClass);
  return { state: "available", usdm_class: usdmClass, week_of: result.servedDay.slice(0, 10) };
}

const weatherRowSchema = z.object({
  latitude: z.number().finite(),
  longitude: z.number().finite(),
  observed_at: z.string(),
  temperature_c: z.number().nullable().optional(),
  relative_humidity_pct: z.number().nullable().optional(),
});

/**
 * Weather section (CONTRACT C5.1, agri `read_weather_section`): the newest published of today and
 * yesterday; within it the nearest station within 50 km, ties to the newest reading. A reading
 * without both temperature and humidity is `read_failed`: the brief never invents a value.
 */
export function siteBriefWeatherSection(
  lat: number,
  lon: number,
  today: string,
  read: PromiseSettledResult<readonly ParquetPlaneEnvelope[]>,
): SiteBriefInputs["weather"] {
  if (read.status === "rejected") return { state: "unavailable", reason: siteBriefFailureReason(read.reason) };
  const envelopes = read.value;
  if (envelopes.length > 0 && envelopes.every((envelope) => envelope.state === "lane_never_written")) {
    return { state: "unavailable", reason: "lane_never_written" };
  }
  for (let back = 0; back <= WEATHER_DAYS_BACK; back += 1) {
    const day = shiftCalendarDay(today, -back);
    const envelope = envelopes.find((entry) => entry.requestedDay === day);
    if (envelope === undefined || envelope.state !== "published") continue;
    if (envelope.truncated) return { state: "unavailable", reason: "read_failed" };
    const parsed = z.array(weatherRowSchema).safeParse(envelope.rows);
    if (!parsed.success) return { state: "unavailable", reason: "read_failed" };
    const ranked = parsed.data
      .map((row) => ({ row, distance: halfUp(haversineMeters(lon, lat, row.longitude, row.latitude)), instant: Date.parse(row.observed_at) }))
      .filter((entry) => entry.distance <= WEATHER_RADIUS_METERS)
      .sort((first, second) => first.distance - second.distance
        || (Number.isFinite(second.instant) ? second.instant : 0) - (Number.isFinite(first.instant) ? first.instant : 0));
    const nearest = ranked[0];
    if (nearest === undefined) return { state: "unavailable", reason: "no_observation_within_radius" };
    const { temperature_c: temperature, relative_humidity_pct: humidity } = nearest.row;
    if (!Number.isFinite(nearest.instant) || typeof temperature !== "number" || !Number.isFinite(temperature)
      || typeof humidity !== "number" || !Number.isFinite(humidity)) {
      return { state: "unavailable", reason: "read_failed" };
    }
    return {
      state: "available",
      observed_at: new Date(nearest.instant).toISOString().replace(/\.\d{3}Z$/, "Z"),
      distance_m: nearest.distance,
      temperature_tenths_c: halfUp(temperature * 10),
      relative_humidity_pct: halfUp(humidity),
    };
  }
  return { state: "unavailable", reason: "not_published" };
}

const cropCellRowSchema = z.object({
  observed_year: z.number().int(),
  release_day: z.string().regex(/^\d{4}-\d{2}-\d{2}/),
  aggregation_cell_m: z.number().positive(),
  cell_area_ha: z.number().positive(),
  class_areas_json: z.string(),
  class_names_json: z.string().nullable().optional(),
  geometry_wkb: z.unknown(),
});

/**
 * Land-cover section: the dominant CDL class (all classes, ties to the lower code) of the z13
 * crop-cover cell containing the point. A dominant class without a name is `read_failed`, never
 * its numeric code (CONTRACT C5.1).
 */
export function siteBriefLandCoverSection(
  lat: number,
  lon: number,
  read: PromiseSettledResult<ParquetPlaneEnvelope | null>,
): SiteBriefInputs["land_cover"] {
  if (read.status === "rejected") return { state: "unavailable", reason: siteBriefFailureReason(read.reason) };
  const envelope = read.value;
  if (envelope === null) return { state: "unavailable", reason: "not_bound_in_region" };
  if (envelope.state === "lane_never_written") return { state: "unavailable", reason: "lane_never_written" };
  if (envelope.state !== "published") return { state: "unavailable", reason: "not_published" };
  const parsed = z.array(cropCellRowSchema).safeParse(envelope.rows);
  if (!parsed.success) return { state: "unavailable", reason: "read_failed" };
  let cell: z.infer<typeof cropCellRowSchema> | undefined;
  try {
    cell = parsed.data.find((row) => geometryContainsPoint(decodePublishedGeometry(row.geometry_wkb), lat, lon));
  } catch {
    return { state: "unavailable", reason: "read_failed" };
  }
  if (cell === undefined) return { state: "unavailable", reason: envelope.truncated ? "read_failed" : "outside_release_coverage" };
  let areas: Record<string, unknown>;
  let names: Record<string, unknown>;
  try {
    areas = JSON.parse(cell.class_areas_json) as Record<string, unknown>;
    names = JSON.parse(cell.class_names_json ?? "{}") as Record<string, unknown>;
  } catch {
    return { state: "unavailable", reason: "read_failed" };
  }
  let dominant: { code: number; area: number } | null = null;
  for (const [code, area] of Object.entries(areas)) {
    const classCode = Number(code);
    if (!Number.isInteger(classCode) || typeof area !== "number" || !Number.isFinite(area) || area <= 0) continue;
    if (dominant === null || area > dominant.area || (area === dominant.area && classCode < dominant.code)) {
      dominant = { code: classCode, area };
    }
  }
  if (dominant === null) return { state: "unavailable", reason: "outside_release_coverage" };
  const className = printableAscii(String(names[String(dominant.code)] ?? ""));
  if (className.length === 0) return { state: "unavailable", reason: "read_failed" };
  return {
    state: "available",
    class_name: className,
    class_code: dominant.code,
    fraction_permille: Math.min(1000, halfUp((dominant.area * 1000) / cell.cell_area_ha)),
    edition_year: cell.observed_year,
    release_day: cell.release_day.slice(0, 10),
    cell_m: Math.round(cell.aggregation_cell_m),
  };
}

/* ---------------------------------------------------------------------------
 * Readers (each takes the scheduler's signal)
 * ------------------------------------------------------------------------- */

const detectionRowSchema = z.object({
  cell_longitude: z.number().finite(),
  cell_latitude: z.number().finite(),
  detection_count: z.number().int().nonnegative(),
});

/** FIRMS detections within 10 km over the 30 UTC days ending today; unpublished days add nothing. */
async function readRecentDetectionCount(lat: number, lon: number, today: string, signal: AbortSignal): Promise<number> {
  const envelopes = await getParquetLayerDayWindow({
    layer: "fire-detections",
    kind: "observed",
    zoomTier: 13,
    bbox: radiusBbox(lat, lon, FIRE_DETECTION_RADIUS_METERS),
    firstDay: shiftCalendarDay(today, -(FIRE_DETECTION_WINDOW_DAYS - 1)),
    lastDay: today,
    signal,
  });
  if (envelopes.length > 0 && envelopes.every((envelope) => envelope.state === "lane_never_written")) {
    throw new SiteBriefRefusal("lane_never_written");
  }
  let count = 0;
  for (const envelope of envelopes) {
    if (envelope.state !== "published") continue;
    // A capped day is an undercount, and an undercount is not a statement about the site.
    if (envelope.truncated) throw new SiteBriefRefusal("read_failed");
    for (const row of z.array(detectionRowSchema).parse(envelope.rows)) {
      if (haversineMeters(lon, lat, row.cell_longitude, row.cell_latitude) <= FIRE_DETECTION_RADIUS_METERS) count += row.detection_count;
    }
  }
  return count;
}

/** The z13 crop-cover release around the point, or null when the region binds no crop-cover layer. */
async function readCropCell(lat: number, lon: number, today: string, signal: AbortSignal): Promise<ParquetPlaneEnvelope | null> {
  if (!getRegion().enabledLayers.some((binding) => binding.layerSlug === CROP_LAYER)) return null;
  return getParquetLatestRelease({
    layer: CROP_LAYER, kind: "observed", zoomTier: 13, asOfDay: today,
    bbox: pointBbox(lat, lon, CROP_HALF_WIDTH_DEGREES), signal,
  });
}

/**
 * Every brief section at the point under one scheduler: soil first, then fire, drought, weather and
 * land cover. `soil` is the request's one soil read when the caller already started it.
 */
export async function readSiteBriefInputs(
  lat: number,
  lon: number,
  today: string,
  scheduler: SiteBriefReadScheduler,
  soil?: Promise<SoilRead>,
): Promise<SiteBriefInputs> {
  const containment = pointBbox(lat, lon, CONTAINMENT_HALF_WIDTH_DEGREES);
  const [soilRead, perimeters, burns, detections, drought, weather, crop] = await Promise.allSettled([
    soil ?? scheduler.run((signal) => getSoilProperties(lat, lon, { timeoutMs: SITE_BRIEF_READ_DEADLINE_MS, signal })),
    scheduler.run((signal) => getParquetFirePerimeters({ bbox: containment, mapZoom: 13, signal })),
    scheduler.run((signal) => getParquetBurnSeverity({ bbox: containment, mapZoom: 13, signal })),
    scheduler.run((signal) => readRecentDetectionCount(lat, lon, today, signal)),
    scheduler.run((signal) => getParquetDrought({ bbox: containment, mapZoom: 13, signal })),
    scheduler.run((signal) => getParquetLayerDayWindow({
      layer: "weather-observations", kind: "observed", zoomTier: 13, bbox: radiusBbox(lat, lon, WEATHER_RADIUS_METERS),
      firstDay: shiftCalendarDay(today, -WEATHER_DAYS_BACK), lastDay: today, signal,
    })),
    scheduler.run((signal) => readCropCell(lat, lon, today, signal)),
  ]);
  return {
    built_on: today,
    point: siteBriefPoint(lat, lon),
    soil: siteBriefSoilSection(soilRead),
    fire: siteBriefFireSection(lat, lon, today, perimeters, burns, detections),
    drought: siteBriefDroughtSection(lat, lon, drought),
    weather: siteBriefWeatherSection(lat, lon, today, weather),
    land_cover: siteBriefLandCoverSection(lat, lon, crop),
  };
}

/* ---------------------------------------------------------------------------
 * The brief cache (review M5)
 * ------------------------------------------------------------------------- */

/** Short, so the nearest weather reading is never frozen for a day. */
export const SITE_BRIEF_CACHE_TTL_MS = 45 * 60 * 1_000;
const SITE_BRIEF_CACHE_CAPACITY = 256;
const SITE_BRIEF_CACHE_CELL_DEGREES = 0.005;
const SITE_BRIEF_CACHE_FLOOR_SNAP_TOLERANCE = 1e-9;
/**
 * Reasons that state a stable fact about the point or the configuration; every other reason (timeout,
 * serving_at_capacity, read_failed, lane_never_written, not_published) is a refusal and is never cached.
 * `reads_disabled` is stable because both flags are in the cache key.
 */
export const SITE_BRIEF_CACHEABLE_UNAVAILABLE_REASONS: readonly SiteBriefReason[] = [
  "no_cell_within_radius", "no_observation_within_radius", "not_bound_in_region", "outside_release_coverage", "reads_disabled",
];

interface CachedSiteBrief {
  soil: SoilRead;
  siteBrief: SiteBrief;
  storedAt: number;
}
// LRU: Map insertion order is recency.
const siteBriefCache = new Map<string, CachedSiteBrief>();

/** Keyed by BOTH flags (CONTRACT C2: the switch is checked before any cache), the 0.005 cell and the UTC day. */
function siteBriefCacheKey(lat: number, lon: number, today: string): string {
  const cellLon = Math.floor(lon / SITE_BRIEF_CACHE_CELL_DEGREES + SITE_BRIEF_CACHE_FLOOR_SNAP_TOLERANCE);
  const cellLat = Math.floor(lat / SITE_BRIEF_CACHE_CELL_DEGREES + SITE_BRIEF_CACHE_FLOOR_SNAP_TOLERANCE);
  return `${cellLon}:${cellLat}:${today}:soil=${soilReadsEnabled()}:brief=${siteBriefEnabled()}`;
}

function siteBriefCacheGet(key: string, now: number): CachedSiteBrief | null {
  const entry = siteBriefCache.get(key);
  if (entry === undefined) return null;
  siteBriefCache.delete(key);
  if (now - entry.storedAt > SITE_BRIEF_CACHE_TTL_MS) return null;
  siteBriefCache.set(key, entry);
  return entry;
}

function siteBriefCachePut(key: string, entry: CachedSiteBrief): void {
  siteBriefCache.delete(key);
  siteBriefCache.set(key, entry);
  while (siteBriefCache.size > SITE_BRIEF_CACHE_CAPACITY) {
    const oldest = siteBriefCache.keys().next().value;
    if (oldest === undefined) break;
    siteBriefCache.delete(oldest);
  }
}

/** A brief is cached only when no section refused: every unavailable section states a plane fact. */
export function siteBriefCacheable(brief: SiteBrief): boolean {
  return [brief.soil, brief.fire, brief.drought, brief.weather, brief.land_cover].every((section) =>
    section.state === "available" || SITE_BRIEF_CACHEABLE_UNAVAILABLE_REASONS.includes(section.reason));
}

/** Test seam: forget every cached brief. */
export function resetSiteBriefCacheForTests(): void {
  siteBriefCache.clear();
}

/* ---------------------------------------------------------------------------
 * One request's brief and soil read
 * ------------------------------------------------------------------------- */

export interface SiteBriefForRequest {
  /** The request's one soil read, shared with `payload.soilProperties`. */
  soil: SoilRead;
  /** Null when SITE_BRIEF_ENABLED is off, or on a follow-up with no cached brief. */
  siteBrief: SiteBrief | null;
}

/**
 * The brief and the one soil read for one `assembleRegionalContext` call (soil/AGENTS.md §site-brief):
 * - SITE_BRIEF_ENABLED off: the soil read alone (itself `reads_disabled` without a read when its flag is off);
 * - a cached brief (either run): served with no read at all;
 * - a follow-up with no cached brief: the soil read alone, never the brief's other reads;
 * - a base run: every section under one bounded scheduler, then cached when no section refused.
 * `soil` is the caller's already-started soil read, when it has one.
 */
export async function siteBriefForRequest(
  lat: number,
  lon: number,
  today: string,
  isBaseRun: boolean,
  soil?: Promise<SoilRead>,
): Promise<SiteBriefForRequest> {
  // `getSoilProperties` never throws; a caller's own promise is still settled into a reason, never a throw.
  const settle = (read: Promise<SoilRead>): Promise<SoilRead> =>
    read.catch((error: unknown) => ({ state: "unavailable" as const, reason: siteBriefFailureReason(error) }));
  const soilOnly = async (): Promise<SiteBriefForRequest> => ({
    soil: await settle(soil ?? getSoilProperties(lat, lon, { timeoutMs: SITE_BRIEF_READ_DEADLINE_MS })),
    siteBrief: null,
  });
  if (!siteBriefEnabled()) return soilOnly();
  const key = siteBriefCacheKey(lat, lon, today);
  const now = Date.now();
  const cached = siteBriefCacheGet(key, now);
  if (cached !== null) return { soil: soil === undefined ? cached.soil : await settle(soil), siteBrief: cached.siteBrief };
  if (!isBaseRun) return soilOnly();
  const scheduler = new SiteBriefReadScheduler();
  const soilRead = soil ?? scheduler.run((signal) => getSoilProperties(lat, lon, { timeoutMs: SITE_BRIEF_READ_DEADLINE_MS, signal }));
  try {
    const inputs = await readSiteBriefInputs(lat, lon, today, scheduler, soilRead);
    const settledSoil = await settle(soilRead);
    let siteBrief: SiteBrief;
    try {
      siteBrief = buildSiteBrief(inputs);
    } catch (error) {
      // Fail open (agri's review m4 rule): an unexpected input shape costs the brief, never the request.
      console.warn("[site-brief] brief could not be built", error instanceof Error ? error.name : typeof error);
      return { soil: settledSoil, siteBrief: null };
    }
    if (siteBriefCacheable(siteBrief)) siteBriefCachePut(key, { soil: settledSoil, siteBrief, storedAt: now });
    return { soil: settledSoil, siteBrief };
  } finally {
    scheduler.dispose();
  }
}
