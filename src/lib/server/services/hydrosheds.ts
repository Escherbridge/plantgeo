import { getRedis } from "@/lib/server/redis";

const WATERSHED_CACHE_TTL = 60 * 60; // 1 hour in seconds

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

  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 3600 },
  });

  if (!response.ok) {
    throw new Error(
      `USGS NHD WFS error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as GeoJSON.FeatureCollection;

  await r.setex(cacheKey, WATERSHED_CACHE_TTL, JSON.stringify(data));

  return data;
}
