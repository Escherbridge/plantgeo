import { and, eq } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/lib/server/db";
import { soilGridCache } from "@/lib/server/db/schema";
import {
  fetchBoundedJson,
  UpstreamHttpError,
  UpstreamPayloadError,
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
const REQUEST_TIMEOUT_MS = 30_000;
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
 * Reads the six topsoil properties out of a SoilGrids response.
 *
 * Values arrive as integers scaled by the per-layer `d_factor`, which is read
 * from the response rather than hardcoded. Returns null when any property is
 * missing: SoilGrids reports `mean: null` outside its coverage, and a partial
 * profile is dropped rather than back-filled -- every consumer (USLE K-factor,
 * carbon potential) treats these as jointly measured.
 */
function parseSoilProperties(payload: unknown): SoilProperties | null {
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

  const complete = Object.values(PROPERTY_FIELDS).every(
    (field) => measured[field] !== undefined
  );
  return complete ? (measured as SoilProperties) : null;
}

/**
 * Returns topsoil (0-5 cm) properties for a point, cached per grid cell.
 *
 * A verified coverage gap is cached as `complete = false` and re-raised as
 * {@link SoilEvidenceUnavailableError} on later reads, so a no-data cell does
 * not re-query ISRIC on every request. Transport failures are never cached.
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

  const [cached] = await db
    .select()
    .from(soilGridCache)
    .where(and(eq(soilGridCache.lat, cellLat), eq(soilGridCache.lon, cellLon)))
    .limit(1);

  const cachedAtMs = cached?.cachedAt?.getTime() ?? 0;
  if (cached && Date.now() - cachedAtMs < CACHE_TTL_MS) {
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

  const url = buildQueryUrl(cellLat, cellLon);
  let payload: unknown;
  try {
    payload = await fetchBoundedJson(
      url,
      { method: "GET", headers: { Accept: "application/json" } },
      { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS }
    );
  } catch (error) {
    // ISRIC throttles aggressively; a 429/5xx is a transport fault, not a
    // statement about coverage, so it must not be written to the cache.
    if (
      error instanceof UpstreamHttpError &&
      (error.status === 429 || error.status >= 500)
    ) {
      throw new SoilUpstreamUnavailableError();
    }
    throw error;
  }

  const measured = parseSoilProperties(payload);

  await db
    .insert(soilGridCache)
    .values({
      lat: cellLat,
      lon: cellLon,
      ph: measured?.ph ?? null,
      organicCarbon: measured?.organicCarbon ?? null,
      nitrogen: measured?.nitrogen ?? null,
      bulkDensity: measured?.bulkDensity ?? null,
      cec: measured?.cec ?? null,
      ocd: measured?.ocd ?? null,
      complete: measured !== null,
      sourceUrl: url.toString(),
    })
    .onConflictDoUpdate({
      target: [soilGridCache.lat, soilGridCache.lon],
      set: {
        ph: measured?.ph ?? null,
        organicCarbon: measured?.organicCarbon ?? null,
        nitrogen: measured?.nitrogen ?? null,
        bulkDensity: measured?.bulkDensity ?? null,
        cec: measured?.cec ?? null,
        ocd: measured?.ocd ?? null,
        complete: measured !== null,
        sourceUrl: url.toString(),
        cachedAt: new Date(),
      },
    });

  if (!measured) {
    throw new SoilEvidenceUnavailableError(
      "SoilGrids reports no soil measurement at this location"
    );
  }
  return measured;
}
