/**
 * Vegetation client-safe helpers — NDVI / NDWI tile URL builders and color ramps.
 * Safe to import from client components (no Node.js dependencies).
 */

export interface NDVIColorStop {
  value: number;
  color: string;
  label: string;
}

// Activation requires a database-backed, immutable publication catalog.
export const ENVIRONMENTAL_TILES_CONFIGURED = false;

export function getEnvironmentalTileTemplate(_path: string): string {
  return "";
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
 * Returns the first-party published NDVI tile template, or an empty string.
 */
export function getNDVITileUrl(
  year: number,
  month: number,
  mode: "absolute" | "anomaly" = "absolute"
): string {
  const mm = String(month).padStart(2, "0");
  return getEnvironmentalTileTemplate(`vegetation/ndvi/${mode}/${year}/${mm}/{z}/{x}/{y}.png`);
}

/**
 * Returns the first-party published NDWI tile template, or an empty string.
 */
export function getNDWITileUrl(year: number, month: number): string {
  const mm = String(month).padStart(2, "0");
  return getEnvironmentalTileTemplate(`vegetation/ndwi/${year}/${mm}/{z}/{x}/{y}.png`);
}

/**
 * Compatibility alias for the first-party published NDVI tile template.
 */
export function getCopernicusNDVITileUrl(year: number, month: number): string {
  return getNDVITileUrl(year, month, "absolute");
}
