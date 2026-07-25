export {
  DEGRADED_NLCD_CLASSES,
  NLCD_CATEGORY_CLASSES,
  NLCD_CLASSES,
  type NLCDCategory,
  type NLCDClass,
} from "@/lib/environmental/nlcd";

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
