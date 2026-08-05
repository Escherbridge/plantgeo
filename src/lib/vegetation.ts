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
// Still false: no first-party tile release (NBR, NLCD land cover) is published.
export const ENVIRONMENTAL_TILES_CONFIGURED = false;

export function getEnvironmentalTileTemplate(_path: string): string {
  return "";
}

/**
 * NASA EOSDIS GIBS NDVI product actually served as web-mercator raster tiles.
 * Public, keyless, CORS-open. See `src/lib/server/AGENTS.md` §vegetation-tiles.
 */
export const GIBS_NDVI_PRODUCT = {
  layerIdentifier: "MODIS_Terra_NDVI_8Day",
  tileMatrixSet: "GoogleMapsCompatible_Level9",
  /** GIBS publishes this product no deeper than zoom 9. */
  maxZoom: 9,
  /** Each tile is a rolling composite of the 8 days ending at the requested date. */
  compositeWindowDays: 8,
  attribution: "NASA EOSDIS GIBS — MODIS/Terra NDVI 8-Day",
} as const;

/**
 * GIBS publishes `MODIS_Terra_NDVI_8Day` from 2025-02-12 only, not from the
 * MODIS/Terra mission start in 2000. Earlier dates 404 rather than returning a
 * blank tile. Re-verify against the `Time` dimension in
 * `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml`.
 * February 2025 is representable because the representative day below is the
 * 15th, which falls inside the extent.
 */
const GIBS_NDVI_FIRST_YEAR = 2025;
const GIBS_NDVI_FIRST_MONTH = 2;

/** A month is requested as its midpoint; the tile is the 8 days ending there. */
const REPRESENTATIVE_DAY_OF_MONTH = 15;

/**
 * Resolves the GIBS TIME value for a requested month, or null when GIBS has no
 * coverage. Returns "default" (GIBS' newest available composite) for the current
 * month, because a mid-month date in the current month may not exist yet.
 * Dates outside the product extent 404 at GIBS rather than returning a blank
 * tile, so an out-of-range month is refused here instead of emitting a dead URL.
 */
export function resolveGibsNdviDate(
  year: number,
  month: number,
  now = new Date()
): string | null {
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    return null;
  }
  if (year < GIBS_NDVI_FIRST_YEAR) return null;
  if (year === GIBS_NDVI_FIRST_YEAR && month < GIBS_NDVI_FIRST_MONTH) return null;

  const currentYear = now.getUTCFullYear();
  const currentMonth = now.getUTCMonth() + 1;
  if (year > currentYear || (year === currentYear && month > currentMonth)) {
    return null;
  }
  if (year === currentYear && month === currentMonth) return "default";
  return `${year}-${String(month).padStart(2, "0")}-${REPRESENTATIVE_DAY_OF_MONTH}`;
}

/**
 * Builds the first-party NDVI tile template for a resolved TIME value.
 * The browser never calls GIBS directly — `/api/tiles/vegetation/ndvi` proxies
 * it so attribution and caching stay first-party. The upstream orders its path
 * as {TileMatrix}/{TileRow}/{TileCol}, which is MapLibre's {z}/{y}/{x}; the
 * placeholders are left for MapLibre to substitute.
 */
export function getGibsNDVITileTemplate(time: string): string {
  return `/api/tiles/vegetation/ndvi/${time}/{z}/{y}/{x}`;
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
 * Returns the NASA GIBS NDVI tile template, or an empty string when unavailable.
 * "anomaly" is always empty: GIBS publishes absolute NDVI only, and deriving an
 * anomaly needs a climatological baseline this platform has not published.
 */
export function getNDVITileUrl(
  year: number,
  month: number,
  mode: "absolute" | "anomaly" = "absolute",
  now = new Date()
): string {
  if (mode === "anomaly") return "";
  const time = resolveGibsNdviDate(year, month, now);
  return time ? getGibsNDVITileTemplate(time) : "";
}

/**
 * Always an empty string: GIBS publishes no NDWI (or any water-index) raster
 * layer, and no first-party NDWI release exists. Rendering a substitute such as
 * a land/water mask would report something the source never measured.
 */
export function getNDWITileUrl(_year: number, _month: number): string {
  return "";
}

/**
 * Why NDWI is offered as a mode but never renders anything -- shown next to the control in
 * VegetationPanel, not only in a title attribute, because a disabled control isn't
 * focusable. Restates getNDWITileUrl's doc comment; keep the two in sync.
 */
export const NDWI_UNAVAILABLE_REASON =
  "NASA GIBS publishes no NDWI (or any water-index) raster, and no first-party NDWI release exists.";

/**
 * Why NDVI "anomaly" mode is offered but never renders anything -- shown next to the
 * control in VegetationPanel, not only in a title attribute. Restates getNDVITileUrl's
 * doc comment; keep the two in sync.
 */
export const NDVI_ANOMALY_UNAVAILABLE_REASON =
  "GIBS publishes absolute NDVI only; no climatological baseline is published to compute an anomaly.";

/**
 * Compatibility alias for the first-party published NDVI tile template.
 */
export function getCopernicusNDVITileUrl(year: number, month: number): string {
  return getNDVITileUrl(year, month, "absolute");
}
