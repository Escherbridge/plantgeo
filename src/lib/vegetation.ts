/**
 * Vegetation client-safe helpers — NDVI / NDWI tile URL builders and color ramps.
 * Safe to import from client components (no Node.js dependencies).
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
 * Returns NASA GIBS MODIS Terra NDVI monthly composite tile URL (WMS format).
 */
export function getNDVITileUrl(
  year: number,
  month: number,
  mode: "absolute" | "anomaly" = "absolute"
): string {
  const mm = String(month).padStart(2, "0");
  const dateStr = `${year}-${mm}-01`;
  const layer = mode === "anomaly"
    ? "MODIS_Terra_L3_NDVI_Monthly"
    : "MODIS_Terra_L3_NDVI_Monthly";

  return (
    "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi?" +
    "SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap" +
    `&LAYERS=${layer}` +
    "&FORMAT=image/png&TRANSPARENT=true" +
    "&SRS=EPSG:3857&WIDTH=256&HEIGHT=256" +
    `&BBOX={bbox-epsg-3857}` +
    `&TIME=${dateStr}`
  );
}

/**
 * Returns NASA GIBS MODIS EVI monthly composite tile URL as a water stress proxy (WMS format).
 * Note: GIBS does not expose a direct NDWI layer; EVI is used as a proxy for water stress.
 */
export function getNDWITileUrl(year: number, month: number): string {
  const mm = String(month).padStart(2, "0");
  const dateStr = `${year}-${mm}-01`;
  return (
    "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi?" +
    "SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap" +
    "&LAYERS=MODIS_Terra_L3_EVI_Monthly" +
    "&FORMAT=image/png&TRANSPARENT=true" +
    "&SRS=EPSG:3857&WIDTH=256&HEIGHT=256" +
    `&BBOX={bbox-epsg-3857}` +
    `&TIME=${dateStr}`
  );
}

/**
 * Copernicus Global Land Service NDVI 300m tile URL (requires Copernicus auth token).
 */
export function getCopernicusNDVITileUrl(year: number, month: number): string {
  const mm = String(month).padStart(2, "0");
  return `https://land.copernicus.eu/global/sites/cgls.vito.be/files/products/CGLOPS1_MAP_NDVI300m-V2_Globe_${year}_${mm}_decade1/{z}/{x}/{y}.png`;
}
