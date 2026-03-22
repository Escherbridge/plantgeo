/**
 * NLCD (National Land Cover Database) service.
 * Provides class definitions, WMS parameter helpers, and change detection config.
 */

export interface NLCDClass {
  code: number;
  name: string;
  color: string;
  category: NLCDCategory;
}

export type NLCDCategory =
  | "Forest"
  | "Shrubland"
  | "Grassland"
  | "Cropland"
  | "Developed"
  | "Wetland"
  | "Other";

/** NLCD 2021 land cover class definitions */
export const NLCD_CLASSES: Record<number, NLCDClass> = {
  11: { code: 11, name: "Open Water", color: "#466b9f", category: "Other" },
  21: { code: 21, name: "Developed, Open Space", color: "#d99482", category: "Developed" },
  22: { code: 22, name: "Developed, Low Intensity", color: "#cd0000", category: "Developed" },
  23: { code: 23, name: "Developed, Medium Intensity", color: "#ab0000", category: "Developed" },
  24: { code: 24, name: "Developed, High Intensity", color: "#730000", category: "Developed" },
  31: { code: 31, name: "Barren Land", color: "#b2ada8", category: "Other" },
  41: { code: 41, name: "Deciduous Forest", color: "#68ab5f", category: "Forest" },
  42: { code: 42, name: "Evergreen Forest", color: "#1c5f2c", category: "Forest" },
  43: { code: 43, name: "Mixed Forest", color: "#b5c58f", category: "Forest" },
  52: { code: 52, name: "Shrub/Scrub", color: "#ccb879", category: "Shrubland" },
  71: { code: 71, name: "Herbaceous/Grassland", color: "#dfdfc2", category: "Grassland" },
  81: { code: 81, name: "Hay/Pasture", color: "#dcd939", category: "Cropland" },
  82: { code: 82, name: "Cultivated Crops", color: "#ab6c28", category: "Cropland" },
  90: { code: 90, name: "Woody Wetlands", color: "#b8d9eb", category: "Wetland" },
  95: { code: 95, name: "Emergent Herbaceous Wetlands", color: "#6c9fb8", category: "Wetland" },
};

/** Classes grouped by category for filter UI */
export const NLCD_CATEGORY_CLASSES: Record<NLCDCategory, number[]> = {
  Forest: [41, 42, 43],
  Shrubland: [52],
  Grassland: [71],
  Cropland: [81, 82],
  Developed: [21, 22, 23, 24],
  Wetland: [90, 95],
  Other: [11, 31],
};

/** NLCD classes considered degraded / reforestation candidates */
export const DEGRADED_NLCD_CLASSES = [52, 71, 81];

/**
 * Returns WMS layer name for NLCD 2021 or change detection.
 */
export function getNLCDLayerName(mode: "2021" | "change" = "2021"): string {
  if (mode === "change") return "nlcd_2019_2021_change_l48";
  return "NLCD_2021_Land_Cover_L48";
}

/**
 * Builds WMS query string params for filtered NLCD requests.
 * Pass class codes to request a subset; omit for all classes.
 */
export function getNLCDWMSParams(classes?: number[]): string {
  const layerName = getNLCDLayerName("2021");
  const base = [
    "SERVICE=WMS",
    "REQUEST=GetMap",
    `LAYERS=${layerName}`,
    "FORMAT=image/png",
    "TRANSPARENT=true",
    "VERSION=1.3.0",
    "CRS=EPSG:3857",
    "BBOX={bbox-epsg-3857}",
    "WIDTH=256",
    "HEIGHT=256",
  ];

  if (classes && classes.length > 0) {
    // SLD_BODY filtering is complex; use CQL_FILTER for GeoServer
    base.push(`CQL_FILTER=NLCD_Class IN (${classes.join(",")})`);
  }

  return base.join("&");
}

/** NLCD WMS base URL */
export const NLCD_WMS_BASE = "https://www.mrlc.gov/geoserver/mrlc_display";
export const NLCD_CHANGE_WMS_BASE = "https://www.mrlc.gov/geoserver/mrlc_change";
