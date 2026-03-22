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
