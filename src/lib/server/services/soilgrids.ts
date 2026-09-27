/**
 * SoilGrids v2.0 point reader over the `soil-properties` Parquet lane (CONTRACT C1, C2, C7).
 * Values are model estimates, never measurements. See src/lib/server/services/soil/AGENTS.md.
 */
import { z } from "zod";
import { UpstreamHttpError, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { getParquetLatestRelease } from "./parquet-plane-client";
import { serverCurrentDate } from "./parquet-day";
import {
  readsFlagEnabled,
  SOILGRIDS_DEPTHS,
  SOILGRIDS_PROPERTIES,
  SOILGRIDS_RESOLUTION_M,
  SOILGRIDS_SOURCE,
  soilDepthValues,
  topsoilSummary,
  type SiteBriefReason,
  type SoilDepthValues,
  type SoilGridsDepth,
  type SoilGridsMapped,
  type SoilTopsoil,
} from "./site-brief";

export const SOIL_PROPERTIES_LAYER = "soil-properties";
export const SOIL_PROPERTIES_READS_FLAG = "SOIL_PROPERTIES_READS_ENABLED";
export const DEFAULT_SOIL_RADIUS_METERS = 1_000;
export const MIN_SOIL_RADIUS_METERS = 50;
export const MAX_SOIL_RADIUS_METERS = 2_000;
/** Lattice pitch and half pitch (CONTRACT C1): keys are SW origins, distance uses the centre. */
export const SOIL_CELL_DEGREES = 0.005;
const CELL_DEGREES = SOIL_CELL_DEGREES;
const HALF_CELL_DEGREES = 0.0025;
/** The pinned lattice envelope of this release (CONTRACT C1: origins aligned to (-125, 42)). */
export const SOIL_LATTICE_ENVELOPE = { west: -125, south: 42, east: -111, north: 49 } as const;
/** Haversine sphere radius pinned by CONTRACT C2. */
export const EARTH_RADIUS_METERS = 6_371_008.8;
/** CONTRACT C5.1 reader constants: metres per degree and the cosine floor near the poles. */
const METERS_PER_DEGREE_LATITUDE = 110_574;
const METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR = 111_320;
export const COVERAGE_COSINE_FLOOR = 0.01;
const FLOOR_SNAP_TOLERANCE = 1e-9;
const INTEGRAL_TOLERANCE = 1e-9;
const CACHE_CAPACITY = 1_024;
const CACHE_TTL_MS = 6 * 60 * 60 * 1_000;

/**
 * Point soil properties. The six unscaled fields are the 0-5 cm physical values kept for
 * pre-lane consumers; the rest are optional only so pre-lane literals still type.
 */
export interface SoilProperties {
  ph: number;
  /** g/kg */
  organicCarbon: number;
  /** g/kg */
  nitrogen: number;
  /** g/cm3 */
  bulkDensity: number;
  /** cmol(c)/kg */
  cec: number;
  /** kg/m3 */
  ocd: number;
  basis?: "model_estimate";
  label?: string;
  releaseId?: string;
  distanceM?: number;
  mapped?: SoilGridsMapped;
  depths?: Record<SoilGridsDepth, SoilDepthValues>;
  topsoil?: SoilTopsoil;
}

/** What the lane reader returns on success: every field present. */
export type SoilGridsEstimate = Required<SoilProperties>;

export type SoilRead =
  | { state: "available"; properties: SoilGridsEstimate }
  | { state: "unavailable"; reason: SiteBriefReason; radiusM?: number };

export interface SoilReadOptions {
  radiusMeters?: number;
  timeoutMs?: number;
  /** The caller's cancellation (the brief's shared deadline); an abort reads as `timeout`. */
  signal?: AbortSignal;
}

const COLUMN_DEPTH: Record<SoilGridsDepth, string> = { "0-5cm": "0_5cm", "5-15cm": "5_15cm", "15-30cm": "15_30cm" };

const valueColumns = SOILGRIDS_DEPTHS.flatMap((depth) =>
  SOILGRIDS_PROPERTIES.map((property) => `${property}_${COLUMN_DEPTH[depth]}`));

const soilRowSchema = z.object({
  cell_longitude: z.number().finite(),
  cell_latitude: z.number().finite(),
  source_release: z.string().min(1),
  source_manifest_sha256: z.string().min(1),
  release_day: z.string().regex(/^\d{4}-\d{2}-\d{2}/),
  ...Object.fromEntries(valueColumns.map((column) => [column, z.number().finite()])),
}) as unknown as z.ZodType<SoilRow>;

type SoilRow = {
  cell_longitude: number;
  cell_latitude: number;
  source_release: string;
  source_manifest_sha256: string;
  release_day: string;
} & Record<string, number | string>;

/** Whether `SOIL_PROPERTIES_READS_ENABLED` is exactly `true` after trimming; anything else is off. */
export function soilReadsEnabled(): boolean {
  return readsFlagEnabled(process.env[SOIL_PROPERTIES_READS_FLAG]);
}

function cosineOfLatitude(latitude: number): number {
  return Math.max(Math.cos((latitude * Math.PI) / 180), COVERAGE_COSINE_FLOOR);
}

/**
 * CONTRACT C5.1 coverage rule, identical to agri `soil_properties.outside_release_coverage`: no lattice
 * cell centre can lie within the radius once the point is more than the radius outside the envelope.
 */
export function outsideSoilReleaseCoverage(longitude: number, latitude: number, radiusMeters: number): boolean {
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return true;
  const latitudeMargin = radiusMeters / METERS_PER_DEGREE_LATITUDE;
  const longitudeMargin = radiusMeters / (METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosineOfLatitude(latitude));
  return !(SOIL_LATTICE_ENVELOPE.west - longitudeMargin <= longitude && longitude <= SOIL_LATTICE_ENVELOPE.east + longitudeMargin
    && SOIL_LATTICE_ENVELOPE.south - latitudeMargin <= latitude && latitude <= SOIL_LATTICE_ENVELOPE.north + latitudeMargin);
}

function clampRadius(radiusMeters: number | undefined): number {
  const radius = radiusMeters ?? DEFAULT_SOIL_RADIUS_METERS;
  if (!Number.isFinite(radius)) return DEFAULT_SOIL_RADIUS_METERS;
  return Math.min(MAX_SOIL_RADIUS_METERS, Math.max(MIN_SOIL_RADIUS_METERS, Math.round(radius)));
}

/** Haversine distance on the CONTRACT C2 sphere, in metres. */
export function haversineMeters(longitudeA: number, latitudeA: number, longitudeB: number, latitudeB: number): number {
  const radians = Math.PI / 180;
  const deltaLatitude = (latitudeB - latitudeA) * radians;
  const deltaLongitude = (longitudeB - longitudeA) * radians;
  const term = Math.sin(deltaLatitude / 2) ** 2
    + Math.cos(latitudeA * radians) * Math.cos(latitudeB * radians) * Math.sin(deltaLongitude / 2) ** 2;
  return 2 * EARTH_RADIUS_METERS * Math.asin(Math.min(1, Math.sqrt(term)));
}

/** Bbox from radius (CONTRACT C2): r/110574 + 0.005 deg by r/(111320 cos lat) + 0.005 deg. */
export function soilReadBbox(latitude: number, longitude: number, radiusMeters: number) {
  const halfLatitude = radiusMeters / METERS_PER_DEGREE_LATITUDE + CELL_DEGREES;
  const halfLongitude = radiusMeters / (METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosineOfLatitude(latitude)) + CELL_DEGREES;
  return {
    west: longitude - halfLongitude,
    south: latitude - halfLatitude,
    east: longitude + halfLongitude,
    north: latitude + halfLatitude,
  };
}

/** Nearest cell centre within the radius; ties by lower latitude, then lower longitude (CONTRACT C2). */
export function selectNearestCell<T extends { cell_longitude: number; cell_latitude: number }>(
  rows: readonly T[], latitude: number, longitude: number, radiusMeters: number,
): { row: T; distanceM: number } | null {
  let best: { row: T; distance: number } | null = null;
  for (const row of rows) {
    const distance = haversineMeters(longitude, latitude, row.cell_longitude + HALF_CELL_DEGREES, row.cell_latitude + HALF_CELL_DEGREES);
    if (best === null || distance < best.distance
      || (distance === best.distance && (row.cell_latitude < best.row.cell_latitude
        || (row.cell_latitude === best.row.cell_latitude && row.cell_longitude < best.row.cell_longitude)))) {
      best = { row, distance };
    }
  }
  if (best === null || best.distance > radiusMeters) return null;
  return { row: best.row, distanceM: Math.floor(best.distance + 0.5) };
}

/** Integer contract at z13: every base value is an integral, non-negative mapped value. */
function mappedFromRow(row: SoilRow): SoilGridsMapped | null {
  const mapped = {} as SoilGridsMapped;
  for (const depth of SOILGRIDS_DEPTHS) {
    const values = {} as Record<(typeof SOILGRIDS_PROPERTIES)[number], number>;
    for (const property of SOILGRIDS_PROPERTIES) {
      const raw = row[`${property}_${COLUMN_DEPTH[depth]}`];
      if (typeof raw !== "number" || Math.abs(raw - Math.round(raw)) >= INTEGRAL_TOLERANCE || Math.round(raw) < 0) return null;
      values[property] = Math.round(raw);
    }
    mapped[depth] = values;
  }
  return mapped;
}

/** Build the widened `SoilProperties` from one validated row. */
export function soilEstimateFromMapped(mapped: SoilGridsMapped, releaseId: string, distanceM: number): SoilGridsEstimate {
  const depths = soilDepthValues(mapped);
  const surface = depths["0-5cm"];
  return {
    ph: surface.ph,
    organicCarbon: surface.soc_g_kg,
    nitrogen: surface.nitrogen_g_kg,
    bulkDensity: surface.bdod_g_cm3,
    cec: surface.cec_cmolc_kg,
    ocd: surface.ocd_kg_m3,
    basis: "model_estimate",
    label: `${SOILGRIDS_SOURCE} ${SOILGRIDS_RESOLUTION_M} m model estimate, 0-5 cm, cell centre ${distanceM} m away (release ${releaseId})`,
    releaseId,
    distanceM,
    mapped,
    depths,
    topsoil: topsoilSummary(mapped),
  };
}

/** Floor to the lattice with the shared snap tolerance, so a boundary point keys its own cell. */
function latticeIndex(value: number): number {
  return Math.floor(value / CELL_DEGREES + FLOOR_SNAP_TOLERANCE);
}

type SoilBbox = ReturnType<typeof soilReadBbox>;

interface CachedCell {
  cellLongitude: number;
  cellLatitude: number;
  mapped: SoilGridsMapped;
  releaseId: string;
}

/**
 * LRU cache (Map insertion order is recency), keyed by the query point's own 0.005 cell and radius.
 * Review M4: a neighbour cell is right for ONE point only, so an entry holds either the query cell
 * itself (the nearest centre for every point inside it) or, when the query cell is masked, the
 * candidate rows and their bbox, re-ranked per point by `selectNearestCell`. See soil/AGENTS.md §soil-cache.
 */
type CachedSoil =
  | { kind: "own_cell"; cell: CachedCell }
  | { kind: "candidates"; bbox: SoilBbox; rows: readonly SoilRow[] };

const cache = new Map<string, { entry: CachedSoil; storedAt: number }>();

function cacheKey(latitude: number, longitude: number, radiusMeters: number): string {
  return `${latticeIndex(longitude)}:${latticeIndex(latitude)}:${radiusMeters}`;
}

function cacheGet(key: string, now: number): CachedSoil | null {
  const stored = cache.get(key);
  if (!stored) return null;
  cache.delete(key);
  if (now - stored.storedAt > CACHE_TTL_MS) return null;
  cache.set(key, stored);
  return stored.entry;
}

function cachePut(key: string, entry: CachedSoil, now: number): void {
  cache.delete(key);
  cache.set(key, { entry, storedAt: now });
  while (cache.size > CACHE_CAPACITY) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

/** Test seam: forget every cached read. */
export function resetSoilPropertiesCacheForTests(): void {
  cache.clear();
}

/** Safety margin on the coverage test below, in degrees (about a centimetre). */
const CANDIDATE_COVER_MARGIN_DEGREES = 1e-7;

/**
 * Whether a cached read's bbox holds the origin of every cell whose centre can lie within the radius
 * of this point, so re-ranking the cached candidates is exact. The circle's extent is the spherical
 * cap's: latitude +- d, longitude +- asin(sin d / cos lat), with d the radius as an angle; origins sit
 * half a cell south-west of their centres.
 */
function candidatesCover(bbox: SoilBbox, latitude: number, longitude: number, radiusMeters: number): boolean {
  const angular = radiusMeters / EARTH_RADIUS_METERS;
  const halfLatitude = (angular * 180) / Math.PI;
  const halfLongitude = (Math.asin(Math.min(1, Math.sin(angular) / cosineOfLatitude(latitude))) * 180) / Math.PI;
  const margin = CANDIDATE_COVER_MARGIN_DEGREES;
  return bbox.west + margin <= longitude - halfLongitude - HALF_CELL_DEGREES
    && bbox.east - margin >= longitude + halfLongitude - HALF_CELL_DEGREES
    && bbox.south + margin <= latitude - halfLatitude - HALF_CELL_DEGREES
    && bbox.north - margin >= latitude + halfLatitude - HALF_CELL_DEGREES;
}

/** Map an agri refusal or transport fault onto the reason vocabulary. */
function failureReason(error: unknown, timedOut: boolean): SiteBriefReason {
  if (timedOut || error instanceof UpstreamTimeoutError) return "timeout";
  if (error instanceof UpstreamHttpError && error.bodyText) {
    try {
      const body = JSON.parse(error.bodyText) as { error?: { code?: unknown } };
      const code = body?.error?.code;
      if (code === "serving_at_capacity") return "serving_at_capacity";
      if (code === "read_timed_out") return "timeout";
    } catch {
      return "read_failed";
    }
  }
  return "read_failed";
}

type Unavailable = Extract<SoilRead, { state: "unavailable" }>;
type LaneRows = { state: "rows"; bbox: SoilBbox; rows: SoilRow[] } | Unavailable;
type Selection = { state: "available"; cell: CachedCell; distanceM: number } | Unavailable;

/** One bbox read of the lane: the validated candidate rows, or a reason. */
async function readLane(
  latitude: number, longitude: number, radiusMeters: number, timeoutMs: number | undefined, signal: AbortSignal | undefined,
): Promise<LaneRows> {
  const bbox = soilReadBbox(latitude, longitude, radiusMeters);
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => { timedOut = true; controller.abort(); };
  const timer = timeoutMs === undefined ? null : setTimeout(abort, timeoutMs);
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  try {
    const request = getParquetLatestRelease({
      layer: SOIL_PROPERTIES_LAYER,
      kind: "observed",
      zoomTier: 13,
      asOfDay: serverCurrentDate(),
      bbox: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`,
      signal: controller.signal,
    });
    // Races the abort too, so a stubbed or stalled transport still honours the deadline.
    const envelope = await (timeoutMs === undefined && signal === undefined ? request : Promise.race([
      request,
      new Promise<never>((_, reject) => {
        if (controller.signal.aborted) reject(new Error("soil read deadline"));
        controller.signal.addEventListener("abort", () => reject(new Error("soil read deadline")), { once: true });
      }),
    ]));
    if (envelope.state === "lane_never_written") return { state: "unavailable", reason: "lane_never_written" };
    if (envelope.state !== "published") return { state: "unavailable", reason: "not_published" };
    if (envelope.truncated) return { state: "unavailable", reason: "read_failed" };
    const parsed = z.array(soilRowSchema).safeParse(envelope.rows);
    if (!parsed.success) return { state: "unavailable", reason: "read_failed" };
    return { state: "rows", bbox, rows: parsed.data };
  } catch (error) {
    return { state: "unavailable", reason: failureReason(error, timedOut) };
  } finally {
    if (timer !== null) clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

/** CONTRACT C2 selection over candidate rows, then the z13 integer contract. */
function selectFromRows(rows: readonly SoilRow[], latitude: number, longitude: number, radiusMeters: number): Selection {
  const nearest = selectNearestCell(rows, latitude, longitude, radiusMeters);
  if (nearest === null) return { state: "unavailable", reason: "no_cell_within_radius", radiusM: radiusMeters };
  const mapped = mappedFromRow(nearest.row);
  if (mapped === null) return { state: "unavailable", reason: "read_failed" };
  return {
    state: "available",
    distanceM: nearest.distanceM,
    cell: {
      cellLongitude: nearest.row.cell_longitude,
      cellLatitude: nearest.row.cell_latitude,
      mapped,
      releaseId: `${nearest.row.source_release}/${nearest.row.release_day.slice(0, 10)}`,
    },
  };
}

function soilReadFrom(selection: Selection): SoilRead {
  if (selection.state !== "available") return selection;
  return { state: "available", properties: soilEstimateFromMapped(selection.cell.mapped, selection.cell.releaseId, selection.distanceM) };
}

/**
 * SoilGrids v2.0 model estimate at the nearest lane cell centre within `radiusMeters`.
 * Never throws: every refusal is a C5.6 reason. The kill switch is checked before the cache.
 */
export async function getSoilProperties(lat: number, lon: number, options: SoilReadOptions = {}): Promise<SoilRead> {
  if (!soilReadsEnabled()) return { state: "unavailable", reason: "reads_disabled" };
  const radiusMeters = clampRadius(options.radiusMeters);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180 || outsideSoilReleaseCoverage(lon, lat, radiusMeters)) {
    return { state: "unavailable", reason: "outside_release_coverage" };
  }
  const key = cacheKey(lat, lon, radiusMeters);
  const now = Date.now();
  const cached = cacheGet(key, now);
  if (cached?.kind === "own_cell") {
    const distance = haversineMeters(lon, lat, cached.cell.cellLongitude + HALF_CELL_DEGREES, cached.cell.cellLatitude + HALF_CELL_DEGREES);
    if (distance <= radiusMeters) {
      return { state: "available", properties: soilEstimateFromMapped(cached.cell.mapped, cached.cell.releaseId, Math.floor(distance + 0.5)) };
    }
  }
  if (cached?.kind === "candidates" && candidatesCover(cached.bbox, lat, lon, radiusMeters)) {
    return soilReadFrom(selectFromRows(cached.rows, lat, lon, radiusMeters));
  }
  const read = await readLane(lat, lon, radiusMeters, options.timeoutMs, options.signal);
  if (read.state !== "rows") return read;
  const selection = selectFromRows(read.rows, lat, lon, radiusMeters);
  // Refusals, faults and a corrupt row (read_failed) are never cached; they are re-read.
  if (selection.state === "available" && latticeIndex(selection.cell.cellLongitude) === latticeIndex(lon)
    && latticeIndex(selection.cell.cellLatitude) === latticeIndex(lat)) {
    cachePut(key, { kind: "own_cell", cell: selection.cell }, now);
  } else if (selection.state === "available" || selection.reason === "no_cell_within_radius") {
    cachePut(key, { kind: "candidates", bbox: read.bbox, rows: read.rows }, now);
  }
  return soilReadFrom(selection);
}
