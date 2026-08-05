import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";
import { cacheGeoJSON, getCachedGeoJSON } from "@/lib/server/redis";

const WATERSHED_CACHE_TTL = 60 * 60; // 1 hour in seconds
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 15_000;

// Layer 12 of the NHDPlus_HR MapServer is `WBDHU12` (esriGeometryPolygon) -- the
// HUC12 watershed boundaries this module exists to serve. It is not layer 2, which
// is `NHDPoint` (esriGeometryPoint): querying that returned single hydrographic
// points carrying permanent_identifier/gnis_name/reachcode and no HUC12 attribute at
// all, so every consumer downstream saw an unlabelled point cloud where polygons
// were expected. Verified against the service catalog (`?f=json`) rather than
// inferred. `f=geojson` emits the layer's own lowercase field names (`name`,
// `huc12`, `areasqkm`, `tohuc`, `states`), not the title-case aliases the catalog
// displays -- which is the spelling src/lib/map/hover-fields.ts reads.
const WBDHU12_LAYER_ID = 12;

const WBDHU12_QUERY_ENDPOINT =
  "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer/" +
  `${WBDHU12_LAYER_ID}/query`;

/** Polygons ArcGIS may return per query; more than this sets `truncated`. */
export const MAX_WATERSHED_FEATURES = 500;

/**
 * Viewport ceiling in square degrees, measured rather than guessed — see
 * `src/lib/server/AGENTS.md` §watershed-boundaries.
 */
export const MAX_WATERSHED_BBOX_SQUARE_DEGREES = 1;

export interface WatershedCollection extends GeoJSON.FeatureCollection {
  /** Upstream held more polygons than it returned; the view is a subset. */
  truncated: boolean;
}

/** The hydrography service answered, but not with a feature collection. */
export class WatershedResponseError extends Error {}

/**
 * HUC12 watershed boundaries intersecting the viewport, from USGS NHD+ HR.
 * @param bbox "west,south,east,north"
 */
export async function getWatersheds(bbox: string): Promise<WatershedCollection> {
  const cacheKey = `watersheds:${bbox}`;
  const cached = await getCachedGeoJSON<unknown>(cacheKey);
  if (isWatershedCollection(cached)) return cached;

  const [west, south, east, north] = bbox.split(",").map(Number);
  const query = new URLSearchParams({
    geometry: JSON.stringify({
      xmin: west,
      ymin: south,
      xmax: east,
      ymax: north,
      spatialReference: { wkid: 4326 },
    }),
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields: "*",
    returnGeometry: "true",
    f: "geojson",
    resultRecordCount: String(MAX_WATERSHED_FEATURES),
    // Rounds coordinates to ~0.1 m, far finer than the 1:24,000 boundaries were
    // digitized at, and cuts the response by ~40% -- measured 8.63 MB -> 5.07 MB
    // over a 1 sq deg viewport, which is the difference between fitting inside
    // MAX_RESPONSE_BYTES and failing as an oversized payload.
    geometryPrecision: "6",
  });

  const payload = await fetchBoundedJson(
    `${WBDHU12_QUERY_ENDPOINT}?${query}`,
    { headers: { Accept: "application/json" } },
    {
      maxBytes: MAX_RESPONSE_BYTES,
      timeoutMs: REQUEST_TIMEOUT_MS,
      revalidateSeconds: WATERSHED_CACHE_TTL,
    }
  );

  const collection = toWatershedCollection(payload);
  await cacheGeoJSON(cacheKey, collection, WATERSHED_CACHE_TTL);
  return collection;
}

/**
 * Validates before anything is cached: ArcGIS answers some faults with HTTP 200 and
 * an `error` object, and persisting one would serve the fault for the whole TTL.
 */
function toWatershedCollection(payload: unknown): WatershedCollection {
  if (typeof payload !== "object" || payload === null) {
    throw new WatershedResponseError("Hydrography service returned a non-object body");
  }
  const candidate = payload as {
    features?: unknown;
    exceededTransferLimit?: unknown;
  };
  if (!Array.isArray(candidate.features)) {
    throw new WatershedResponseError("Hydrography service returned no feature array");
  }
  if (candidate.features.some((f) => typeof f !== "object" || f === null)) {
    throw new WatershedResponseError("Hydrography service returned a malformed feature");
  }

  return {
    type: "FeatureCollection",
    // Narrow interop cast: every entry is confirmed to be an object above, and the
    // layer's attributes are passed through verbatim rather than re-modelled.
    features: candidate.features as GeoJSON.Feature[],
    truncated: candidate.exceededTransferLimit === true,
  };
}

/** Guard for a cached payload, which is untrusted like any other external value. */
function isWatershedCollection(value: unknown): value is WatershedCollection {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as { features?: unknown; truncated?: unknown };
  return (
    Array.isArray(candidate.features) && typeof candidate.truncated === "boolean"
  );
}
