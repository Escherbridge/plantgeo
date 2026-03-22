/**
 * Vegetation service — NDVI, NDWI tile URL helpers and color ramps.
 * Uses NASA GIBS as primary source (no auth required, global coverage).
 * Copernicus Global Land Service URLs are provided as reference but require auth.
 */

export interface NDVIColorStop {
  value: number;
  color: string;
  label: string;
}

/** NDVI color ramp: -1 (water/bare) → 0 (sparse) → 1 (dense healthy vegetation) */
export const NDVI_COLOR_RAMP: NDVIColorStop[] = [
  { value: -0.2, color: "#d73027", label: "Water / Bare soil" },
  { value: 0.0, color: "#f46d43", label: "Very sparse" },
  { value: 0.1, color: "#fdae61", label: "Sparse" },
  { value: 0.2, color: "#fee08b", label: "Low density" },
  { value: 0.3, color: "#d9ef8b", label: "Moderate" },
  { value: 0.4, color: "#a6d96a", label: "Moderate-high" },
  { value: 0.5, color: "#66bd63", label: "High density" },
  { value: 0.7, color: "#1a9850", label: "Very dense" },
  { value: 1.0, color: "#006837", label: "Maximum greenness" },
];

/** NDWI color ramp: low water content (red) → high water content (blue) */
export const NDWI_COLOR_RAMP: NDVIColorStop[] = [
  { value: -0.5, color: "#d73027", label: "Severe water stress" },
  { value: -0.2, color: "#f46d43", label: "High water stress" },
  { value: 0.0, color: "#fee08b", label: "Moderate stress" },
  { value: 0.2, color: "#abd9e9", label: "Adequate moisture" },
  { value: 0.4, color: "#74add1", label: "High moisture" },
  { value: 0.6, color: "#4575b4", label: "Water body" },
];

/**
 * Returns NASA GIBS MODIS Terra NDVI monthly composite tile URL.
 * year: full year e.g. 2023
 * month: 1–12
 * mode: 'absolute' returns standard NDVI; 'anomaly' returns NDVI anomaly layer
 */
export function getNDVITileUrl(
  year: number,
  month: number,
  mode: "absolute" | "anomaly" = "absolute"
): string {
  const mm = String(month).padStart(2, "0");
  const dateStr = `${year}-${mm}-01`;

  if (mode === "anomaly") {
    // NASA GIBS NDVI anomaly layer
    return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Combined_NDVI/default/${year}-${month.toString().padStart(2,'0')}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png`;
  }

  // Absolute NDVI monthly composite
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_NDVI_M/default/${dateStr}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png`;
}

/**
 * Returns NASA GIBS MODIS NDWI (water stress) monthly composite tile URL.
 * Uses MODIS Terra Land Surface Temperature / Emissivity as proxy when NDWI
 * is unavailable; for a true NDWI product substitute the layer name below.
 */
export function getNDWITileUrl(year: number, month: number): string {
  const mm = String(month).padStart(2, "0");
  const dateStr = `${year}-${mm}-01`;
  // MODIS Aqua NDWI monthly composite
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Aqua_NDWI_M/default/${dateStr}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png`;
}

/**
 * Copernicus Global Land Service NDVI 300m tile URL (requires Copernicus auth token).
 * Provided for reference — use getNDVITileUrl() (NASA GIBS) as the default.
 */
export function getCopernicusNDVITileUrl(year: number, month: number): string {
  const mm = String(month).padStart(2, "0");
  // Decade 1 composite (1st–10th of month)
  return `https://land.copernicus.eu/global/sites/cgls.vito.be/files/products/CGLOPS1_MAP_NDVI300m-V2_Globe_${year}_${mm}_decade1/{z}/{x}/{y}.png`;
}

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
