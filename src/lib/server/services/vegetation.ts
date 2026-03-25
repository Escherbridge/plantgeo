/**
 * Vegetation server service — re-exports client-safe helpers and adds
 * server-only functions that depend on Redis / Node.js APIs.
 */

// Re-export everything from the shared client-safe module
export {
  type NDVIColorStop,
  NDVI_COLOR_RAMP,
  NDWI_COLOR_RAMP,
  getNDVITileUrl,
  getNDWITileUrl,
  getCopernicusNDVITileUrl,
} from "@/lib/vegetation";

/**
 * Fetches the MODIS Terra NDVI value at a specific lat/lon point for a given
 * year and month using NASA GIBS WMS GetFeatureInfo.
 *
 * Returns a value in the range [-1, 1], or null if the service is unavailable,
 * the pixel is fill/no-data, or the request times out.
 *
 * Results are cached in Redis for 24 hours (monthly product doesn't change).
 */
export async function getNDVIAtPoint(
  lat: number,
  lon: number,
  year?: number,
  month?: number
): Promise<number | null> {
  const y = year ?? new Date().getFullYear();
  const m = month ?? new Date().getMonth() + 1;
  const dateStr = `${y}-${String(m).padStart(2, "0")}-01`;

  // Try Redis cache first
  let redis: import("ioredis").default | null = null;
  try {
    const { getRedis } = await import("@/lib/server/redis");
    redis = getRedis();
  } catch {
    // Redis unavailable — proceed without cache
  }

  const cacheKey = `ndvi-point:${lat.toFixed(2)}:${lon.toFixed(2)}:${y}-${m}`;
  if (redis) {
    try {
      const cached = await redis.get(cacheKey);
      if (cached !== null) return parseFloat(cached);
    } catch {
      // cache miss — proceed to fetch
    }
  }

  try {
    const bbox = `${lon - 0.01},${lat - 0.01},${lon + 0.01},${lat + 0.01}`;
    const url =
      `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?` +
      new URLSearchParams({
        SERVICE: "WMS",
        VERSION: "1.1.1",
        REQUEST: "GetFeatureInfo",
        LAYERS: "MODIS_Terra_NDVI_M",
        QUERY_LAYERS: "MODIS_Terra_NDVI_M",
        INFO_FORMAT: "application/json",
        SRS: "EPSG:4326",
        BBOX: bbox,
        WIDTH: "2",
        HEIGHT: "2",
        X: "1",
        Y: "1",
        TIME: dateStr,
      }).toString();

    const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) return null;

    const data = (await response.json()) as {
      features?: Array<{ properties?: Record<string, unknown> }>;
    };

    // MODIS NDVI raw scale: 0–10000 maps to -1–1; fill value is -3000 or below
    const props = data?.features?.[0]?.properties;
    const raw = props?.GRAY_INDEX ?? props?.["1"] ?? null;
    if (raw === null || raw === undefined || typeof raw !== "number" || raw < -1000) {
      return null;
    }

    const ndvi = raw / 10000;

    if (redis) {
      try {
        await redis.set(cacheKey, ndvi.toString(), "EX", 86400);
      } catch {
        // non-fatal cache write failure
      }
    }

    return ndvi;
  } catch {
    return null;
  }
}
