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

export const NLCD_CLASSES: Readonly<Record<number, NLCDClass>> = {
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

export const NLCD_CATEGORY_CLASSES: Readonly<Record<NLCDCategory, readonly number[]>> = {
  Forest: [41, 42, 43],
  Shrubland: [52],
  Grassland: [71],
  Cropland: [81, 82],
  Developed: [21, 22, 23, 24],
  Wetland: [90, 95],
  Other: [11, 31],
};

export const DEGRADED_NLCD_CLASSES: readonly number[] = [52, 71, 81];
