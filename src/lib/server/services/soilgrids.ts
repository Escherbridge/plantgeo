import { and, eq } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/lib/server/db";
import { soilGridCache } from "@/lib/server/db/schema";
import {
  fetchBoundedJson,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";

export interface SoilProperties {
  ph: number;
  organicCarbon: number;
  nitrogen: number;
  bulkDensity: number;
  cec: number;
  ocd: number;
}

export const SOIL_EVIDENCE_UNAVAILABLE_CODE =
  "VALIDATED_SOIL_RELEASE_NOT_PUBLISHED" as const;

/** Marks a verified upstream coverage gap, not a transport failure. */
export class SoilEvidenceUnavailableError extends Error {
  readonly code = SOIL_EVIDENCE_UNAVAILABLE_CODE;

  constructor(
    message = "SoilGrids reports no soil measurement at this location"
  ) {
    super(message);
    this.name = "SoilEvidenceUnavailableError";
  }
}

/** Marks a transient upstream failure so it is not cached as a coverage gap. */
export class SoilUpstreamUnavailableError extends Error {
  readonly code = "SOIL_UPSTREAM_UNAVAILABLE" as const;

  constructor(message = "SoilGrids is temporarily unavailable") {
    super(message);
    this.name = "SoilUpstreamUnavailableError";
  }
}

const SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query";
const DEPTH_LABEL = "0-5cm";
const MAX_RESPONSE_BYTES = 256 * 1024;
/**
 * ISRIC either answers quickly or not at all, so a long bound only lengthens the
 * spinner before the same failure. See `src/lib/server/AGENTS.md` §soil-evidence.
 */
const REQUEST_TIMEOUT_MS = 12_000;
/**
 * Cache key resolution in degrees. SoilGrids v2.0 is a static 250 m (~0.00225°)
 * raster, so a 0.001° cell is finer than a source pixel: quantizing here picks
 * the cell to query, it does not average or interpolate across cells.
 */
const CACHE_CELL_DEGREES = 0.001;

/** SoilGrids v2.0 is a static release; a cached cell stays valid for a long time. */
const CACHE_TTL_MS = 90 * 24 * 60 * 60 * 1_000;

/** SoilGrids property name -> the SoilProperties field it populates. */
const PROPERTY_FIELDS = {
  phh2o: "ph",
  soc: "organicCarbon",
  nitrogen: "nitrogen",
  bdod: "bulkDensity",
  cec: "cec",
  ocd: "ocd",
} as const satisfies Record<string, keyof SoilProperties>;

const SOILGRIDS_PROPERTIES = Object.keys(PROPERTY_FIELDS) as Array<
  keyof typeof PROPERTY_FIELDS
>;

const SoilGridsResponseSchema = z.object({
  properties: z.object({
    layers: z.array(
      z.object({
        name: z.string(),
        unit_measure: z.object({ d_factor: z.number().finite().positive() }),
        depths: z.array(
          z.object({
            label: z.string(),
            values: z.object({
              mean: z.number().finite().nullable().optional(),
            }),
          })
        ),
      })
    ),
  }),
});

/** Snaps a coordinate onto the cache grid; the returned cell is what gets queried. */
function toCacheCell(value: number): number {
  return Number((Math.round(value / CACHE_CELL_DEGREES) * CACHE_CELL_DEGREES).toFixed(3));
}

function buildQueryUrl(lat: number, lon: number): URL {
  const url = new URL(SOILGRIDS_URL);
  const params = new URLSearchParams({
    lon: String(lon),
    lat: String(lat),
    depth: DEPTH_LABEL,
    value: "mean",
  });
  for (const property of SOILGRIDS_PROPERTIES) params.append("property", property);
  url.search = params.toString();
  return url;
}

/**
 * What a SoilGrids response says about a cell.
 *
 * `coverage-gap` (every property `mean: null`) is a claim the upstream actually
 * made and is safe to cache. `partial` is not: the six properties share one
 * raster extent, so a mixed response is an upstream anomaly rather than a
 * statement about the cell. Caching it as a gap would record a claim SoilGrids
 * never made. See `src/lib/server/AGENTS.md` §soil-evidence.
 */
type SoilReading =
  | { kind: "complete"; properties: SoilProperties }
  | { kind: "coverage-gap" }
  | { kind: "partial"; measuredFields: Array<keyof SoilProperties> };

/**
 * Reads the six topsoil properties out of a SoilGrids response.
 *
 * Values arrive as integers scaled by the per-layer `d_factor`, which is read
 * from the response rather than hardcoded.
 */
function parseSoilReading(payload: unknown): SoilReading {
  const parsed = SoilGridsResponseSchema.safeParse(payload);
  if (!parsed.success) {
    throw new UpstreamPayloadError("SoilGrids returned an unexpected response shape");
  }

  const measured: Partial<SoilProperties> = {};
  for (const layer of parsed.data.properties.layers) {
    const field = PROPERTY_FIELDS[layer.name as keyof typeof PROPERTY_FIELDS];
    if (!field) continue;
    const depth = layer.depths.find((entry) => entry.label === DEPTH_LABEL);
    const mean = depth?.values.mean;
    if (mean === null || mean === undefined) continue;
    measured[field] = mean / layer.unit_measure.d_factor;
  }

  const measuredFields = Object.values(PROPERTY_FIELDS).filter(
    (field) => measured[field] !== undefined
  );
  if (measuredFields.length === Object.values(PROPERTY_FIELDS).length) {
    return { kind: "complete", properties: measured as SoilProperties };
  }
  if (measuredFields.length === 0) return { kind: "coverage-gap" };
  return { kind: "partial", measuredFields };
}

/** Maps a transport fault onto the retryable soil error; other errors pass through. */
function asSoilError(error: unknown): unknown {
  if (error instanceof UpstreamTimeoutError) {
    return new SoilUpstreamUnavailableError("SoilGrids did not respond in time");
  }
  if (
    error instanceof UpstreamHttpError &&
    (error.status === 429 || error.status >= 500)
  ) {
    return new SoilUpstreamUnavailableError();
  }
  if (error instanceof UpstreamPayloadError) {
    return new SoilUpstreamUnavailableError("SoilGrids returned an unreadable response");
  }
  return error;
}

type CachedCell = typeof soilGridCache.$inferSelect;

/** Serves a cached row, re-raising a stored coverage gap as the evidence error. */
function readCachedCell(cached: CachedCell): SoilProperties {
  if (!cached.complete) {
    throw new SoilEvidenceUnavailableError(
      "SoilGrids reports no soil measurement at this location"
    );
  }
  return {
    ph: cached.ph as number,
    organicCarbon: cached.organicCarbon as number,
    nitrogen: cached.nitrogen as number,
    bulkDensity: cached.bulkDensity as number,
    cec: cached.cec as number,
    ocd: cached.ocd as number,
  };
}

/** Writes a cell whose contents SoilGrids actually reported. */
async function writeCachedCell(
  cellLat: number,
  cellLon: number,
  properties: SoilProperties | null,
  sourceUrl: string
): Promise<void> {
  const values = {
    ph: properties?.ph ?? null,
    organicCarbon: properties?.organicCarbon ?? null,
    nitrogen: properties?.nitrogen ?? null,
    bulkDensity: properties?.bulkDensity ?? null,
    cec: properties?.cec ?? null,
    ocd: properties?.ocd ?? null,
    complete: properties !== null,
    sourceUrl,
  };
  await db
    .insert(soilGridCache)
    .values({ lat: cellLat, lon: cellLon, ...values })
    .onConflictDoUpdate({
      target: [soilGridCache.lat, soilGridCache.lon],
      set: { ...values, cachedAt: new Date() },
    });
}

/** Queries ISRIC for one cell, translating transport faults into soil errors. */
async function queryUpstream(cellLat: number, cellLon: number): Promise<{
  reading: SoilReading;
  sourceUrl: string;
}> {
  const url = buildQueryUrl(cellLat, cellLon);
  try {
    const payload = await fetchBoundedJson(
      url,
      { method: "GET", headers: { Accept: "application/json" } },
      { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS }
    );
    return { reading: parseSoilReading(payload), sourceUrl: url.toString() };
  } catch (error) {
    throw asSoilError(error);
  }
}

async function resolveCell(
  cellLat: number,
  cellLon: number
): Promise<SoilProperties> {
  const [cached] = await db
    .select()
    .from(soilGridCache)
    .where(and(eq(soilGridCache.lat, cellLat), eq(soilGridCache.lon, cellLon)))
    .limit(1);

  const cachedAtMs = cached?.cachedAt?.getTime() ?? 0;
  if (cached && Date.now() - cachedAtMs < CACHE_TTL_MS) {
    return readCachedCell(cached);
  }

  let reading: SoilReading;
  let sourceUrl: string;
  try {
    ({ reading, sourceUrl } = await queryUpstream(cellLat, cellLon));
  } catch (error) {
    // SoilGrids v2.0 is a frozen release, so an expired row is still the same
    // measurement: serving it beats failing while ISRIC sheds load. Only rows that
    // actually carry a measurement qualify -- an expired coverage-gap row would make
    // readCachedCell assert "no soil here", turning a retryable transport fault into a
    // claim about the ground.
    if (cached?.complete) return readCachedCell(cached);
    throw error;
  }

  if (reading.kind === "partial") {
    // Not cached: a mixed response is an upstream anomaly, and recording it
    // would either claim a coverage gap or store a profile no consumer can use.
    // Same rule as the transport-fault path above: only serve a stale row that
    // holds a measurement, never a stale coverage gap.
    if (cached?.complete) return readCachedCell(cached);
    throw new SoilUpstreamUnavailableError(
      `SoilGrids returned only ${reading.measuredFields.length} of ` +
        `${SOILGRIDS_PROPERTIES.length} topsoil properties`
    );
  }

  const properties = reading.kind === "complete" ? reading.properties : null;
  await writeCachedCell(cellLat, cellLon, properties, sourceUrl);

  if (!properties) {
    throw new SoilEvidenceUnavailableError(
      "SoilGrids reports no soil measurement at this location"
    );
  }
  return properties;
}

/** In-flight cell queries, so concurrent readers share one upstream request. */
const inFlightCells = new Map<string, Promise<SoilProperties>>();

/**
 * Returns topsoil (0-5 cm) properties for a point, cached per grid cell.
 *
 * A verified coverage gap is cached as `complete = false` and re-raised as
 * {@link SoilEvidenceUnavailableError} on later reads, so a no-data cell does
 * not re-query ISRIC on every request. Transport failures are never cached and
 * fall back to an expired row when one exists.
 */
export async function getSoilProperties(
  lat: number,
  lon: number
): Promise<SoilProperties> {
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new RangeError("Latitude must be between -90 and 90");
  }
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
    throw new RangeError("Longitude must be between -180 and 180");
  }

  const cellLat = toCacheCell(lat);
  const cellLon = toCacheCell(lon);
  const cellKey = `${cellLat},${cellLon}`;

  const inFlight = inFlightCells.get(cellKey);
  if (inFlight) return inFlight;

  const request = resolveCell(cellLat, cellLon).finally(() => {
    inFlightCells.delete(cellKey);
  });
  inFlightCells.set(cellKey, request);
  return request;
}
