import { getRedis } from "@/lib/server/redis";
import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";

const WATERSHED_CACHE_TTL = 60 * 60; // 1 hour in seconds
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 10_000;

/**
 * Fetch HUC12 watershed boundaries from USGS NHD+ HR WFS for a bounding box.
 * Uses the National Hydrography Dataset Plus High Resolution ArcGIS REST service.
 * @param bbox "west,south,east,north"
 */
export async function getWatersheds(
  bbox: string
): Promise<GeoJSON.FeatureCollection> {
  const cacheKey = `watersheds:${bbox}`;
  const r = getRedis();

  const cached = await r.get(cacheKey);
  if (cached) {
    return JSON.parse(cached) as GeoJSON.FeatureCollection;
  }

  // Parse bbox to build esriGeometryEnvelope parameter
  const [west, south, east, north] = bbox.split(",").map(Number);
  const geometry = encodeURIComponent(
    JSON.stringify({
      xmin: west,
      ymin: south,
      xmax: east,
      ymax: north,
      spatialReference: { wkid: 4326 },
    })
  );

  const url =
    `https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer/2/query` +
    `?geometry=${geometry}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326` +
    `&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*` +
    `&returnGeometry=true` +
    `&f=geojson` +
    `&resultRecordCount=500`;

  const data = (await fetchBoundedJson(
    url,
    { headers: { Accept: "application/json" } },
    { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS, revalidateSeconds: 3600 }
  )) as GeoJSON.FeatureCollection;

  await r.setex(cacheKey, WATERSHED_CACHE_TTL, JSON.stringify(data));

  return data;
}
