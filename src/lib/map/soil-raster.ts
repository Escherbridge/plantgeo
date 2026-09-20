/** SoilGrids properties published as independent topsoil raster layers. */
export const SOIL_RASTER_PROPERTIES = [
  "phh2o",
  "soc",
  "nitrogen",
  "bdod",
  "cec",
  "ocd",
] as const;

export type SoilProperty = (typeof SOIL_RASTER_PROPERTIES)[number];
export type SoilRasterToggleId = `soil-${SoilProperty}`;

export const SOIL_PROPERTY_LABELS: Record<SoilProperty, string> = {
  phh2o: "pH (H₂O)",
  soc: "Organic Carbon",
  nitrogen: "Nitrogen",
  bdod: "Bulk Density",
  cec: "CEC",
  ocd: "Organic Carbon Density",
};

export const SOIL_RASTER_TOGGLE_IDS = SOIL_RASTER_PROPERTIES.map(
  (property): SoilRasterToggleId => `soil-${property}`
);

export function soilRasterToggleId(property: SoilProperty): SoilRasterToggleId {
  return `soil-${property}`;
}

export function soilPropertyForToggle(toggleId: string): SoilProperty | null {
  const property = toggleId.startsWith("soil-") ? toggleId.slice(5) : "";
  return (SOIL_RASTER_PROPERTIES as readonly string[]).includes(property)
    ? (property as SoilProperty)
    : null;
}

/** Point-query field corresponding to each published raster. */
export const SOIL_PROPERTY_POINT_FIELD: Record<
  SoilProperty,
  "ph" | "organicCarbon" | "nitrogen" | "bulkDensity" | "cec" | "ocd"
> = {
  phh2o: "ph",
  soc: "organicCarbon",
  nitrogen: "nitrogen",
  bdod: "bulkDensity",
  cec: "cec",
  ocd: "ocd",
};

export interface SoilRasterColorStop {
  value: number;
  color: string;
}

/** Client-safe catalogue fields used by raster sources and legends. */
export interface PublishedSoilRaster {
  property: SoilProperty;
  unit: string;
  scaleDivisor: number;
  valueMin: number | null;
  valueMax: number | null;
  colorRamp: SoilRasterColorStop[];
  archiveUrl: string;
  minZoom: number;
  maxZoom: number;
  attribution: string;
  sourceName: string;
  sourceRelease: string;
  licenseName: string;
  bounds: [number, number, number, number];
}
