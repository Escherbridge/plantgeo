import type { WaterGauge, GroundwaterWell } from "@/lib/server/services/usgs-water";

/**
 * Demo USDM-style drought GeoJSON covering eastern Washington State.
 * Each feature carries a numeric `DM` property (0–4) matching the DroughtLayer
 * choropleth mapping, plus a human-readable `Name` label.
 */
export const DEMO_DROUGHT_GEOJSON: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: { DM: 3, Name: "Yakima Valley Extreme Drought" },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-120.8, 46.5],
            [-120.2, 46.5],
            [-120.2, 46.8],
            [-120.5, 46.85],
            [-120.8, 46.7],
            [-120.8, 46.5],
          ],
        ],
      },
    },
    {
      type: "Feature",
      properties: { DM: 2, Name: "Columbia Basin Severe Drought" },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-119.4, 46.1],
            [-118.8, 46.1],
            [-118.8, 46.4],
            [-119.1, 46.45],
            [-119.4, 46.3],
            [-119.4, 46.1],
          ],
        ],
      },
    },
    {
      type: "Feature",
      properties: { DM: 4, Name: "Okanogan Highlands Exceptional Drought" },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-119.8, 48.1],
            [-119.2, 48.1],
            [-119.2, 48.5],
            [-119.5, 48.55],
            [-119.8, 48.4],
            [-119.8, 48.1],
          ],
        ],
      },
    },
    {
      type: "Feature",
      properties: { DM: 1, Name: "Walla Walla Wheat Belt Moderate Drought" },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-118.6, 45.9],
            [-118.1, 45.9],
            [-118.1, 46.2],
            [-118.35, 46.25],
            [-118.6, 46.1],
            [-118.6, 45.9],
          ],
        ],
      },
    },
  ],
};

/**
 * Demo USGS streamflow gauge readings at approximate real-world Washington
 * NWIS station locations. Conditions and trends span the full classification
 * range for visual testing of the WaterLayer color coding.
 */
export const DEMO_WATER_GAUGES: WaterGauge[] = [
  {
    siteNo: "12500450",
    siteName: "Yakima River at Yakima",
    lat: 46.5982,
    lon: -120.5078,
    flowCfs: 1240,
    percentile: 45,
    condition: "normal",
    trend: "stable",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "12181000",
    siteName: "Skagit River at Marblemount",
    lat: 48.5290,
    lon: -121.4412,
    flowCfs: 3800,
    percentile: 72,
    condition: "above_normal",
    trend: "rising",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "12149000",
    siteName: "Snoqualmie River near Snoqualmie",
    lat: 47.5290,
    lon: -121.8328,
    flowCfs: 890,
    percentile: 35,
    condition: "below_normal",
    trend: "declining",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "12422500",
    siteName: "Spokane River at Spokane",
    lat: 47.6743,
    lon: -117.4233,
    flowCfs: 2100,
    percentile: 55,
    condition: "normal",
    trend: "stable",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "12089500",
    siteName: "Nisqually River near Alder",
    lat: 46.8712,
    lon: -122.3467,
    flowCfs: 520,
    percentile: 18,
    condition: "low",
    trend: "declining",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "12213100",
    siteName: "Nooksack River near Ferndale",
    lat: 48.8570,
    lon: -122.5581,
    flowCfs: 4200,
    percentile: 80,
    condition: "above_normal",
    trend: "rising",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "12025500",
    siteName: "Chehalis River at Grand Mound",
    lat: 46.7640,
    lon: -123.0228,
    flowCfs: 680,
    percentile: 28,
    condition: "below_normal",
    trend: "declining",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "14105700",
    siteName: "Columbia R near The Dalles",
    lat: 45.6121,
    lon: -121.1693,
    flowCfs: 185000,
    percentile: 62,
    condition: "above_normal",
    trend: "rising",
    updatedAt: new Date().toISOString(),
  },
];

/**
 * Demo USGS groundwater well readings at approximate Washington monitoring
 * sites. Depth values and trends exercise the full WaterLayer well color range.
 */
export const DEMO_GROUNDWATER_WELLS: GroundwaterWell[] = [
  {
    siteNo: "GW-530001",
    siteName: "Quincy Basin Well",
    lat: 47.2340,
    lon: -119.8526,
    depthFt: 42.3,
    trend: "declining",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "GW-530002",
    siteName: "Yakima Valley Well",
    lat: 46.5800,
    lon: -120.4800,
    depthFt: 28.7,
    trend: "declining",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "GW-530003",
    siteName: "Spokane Aquifer Well",
    lat: 47.6500,
    lon: -117.3800,
    depthFt: 15.2,
    trend: "stable",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "GW-530004",
    siteName: "Walla Walla Basin Well",
    lat: 46.0700,
    lon: -118.3200,
    depthFt: 55.8,
    trend: "declining",
    updatedAt: new Date().toISOString(),
  },
  {
    siteNo: "GW-530005",
    siteName: "Skagit Valley Well",
    lat: 48.4500,
    lon: -122.3400,
    depthFt: 8.4,
    trend: "rising",
    updatedAt: new Date().toISOString(),
  },
];

/** Shape for a single soil sample point. */
export interface SoilPoint {
  lat: number;
  lon: number;
  /** Clay fraction (0–100 %) */
  clay: number;
  /** Sand fraction (0–100 %) */
  sand: number;
  /** Silt fraction (0–100 %) */
  silt: number;
  /** Organic carbon (g/kg) */
  organicCarbon: number;
  /** pH (0–14) */
  ph: number;
  /** Total nitrogen (g/kg) */
  nitrogen: number;
  /** Bulk density (g/cm³) */
  bulkDensity: number;
  /** Cation exchange capacity (cmol/kg) */
  cec: number;
  /** Organic carbon density (kg/m²) */
  ocd: number;
}

/**
 * Demo soil property sample points spread across Washington agricultural and
 * natural regions for use with soil composition visualizations.
 */
export const DEMO_SOIL_POINTS: SoilPoint[] = [
  // Palouse (Pullman)
  { lat: 46.7298, lon: -117.1817, clay: 28, sand: 15, silt: 57, organicCarbon: 2.8, ph: 6.2, nitrogen: 0.25, bulkDensity: 1.15, cec: 28.5, ocd: 3.2 },
  // Walla Walla
  { lat: 46.0646, lon: -118.3430, clay: 22, sand: 25, silt: 53, organicCarbon: 1.9, ph: 7.0, nitrogen: 0.18, bulkDensity: 1.25, cec: 22.0, ocd: 2.4 },
  // Skagit Valley
  { lat: 48.4740, lon: -122.3265, clay: 35, sand: 20, silt: 45, organicCarbon: 4.2, ph: 5.8, nitrogen: 0.32, bulkDensity: 1.05, cec: 35.0, ocd: 4.4 },
  // Yakima Valley
  { lat: 46.6021, lon: -120.5059, clay: 18, sand: 45, silt: 37, organicCarbon: 1.2, ph: 7.5, nitrogen: 0.12, bulkDensity: 1.40, cec: 15.0, ocd: 1.7 },
  // Olympic Peninsula
  { lat: 47.9512, lon: -124.3847, clay: 30, sand: 25, silt: 45, organicCarbon: 6.5, ph: 5.2, nitrogen: 0.38, bulkDensity: 0.85, cec: 38.0, ocd: 5.5 },
  // Ellensburg
  { lat: 46.9965, lon: -120.5478, clay: 15, sand: 55, silt: 30, organicCarbon: 0.8, ph: 7.8, nitrogen: 0.08, bulkDensity: 1.50, cec: 12.0, ocd: 1.0 },
  // Wenatchee
  { lat: 47.4235, lon: -120.3103, clay: 20, sand: 40, silt: 40, organicCarbon: 1.5, ph: 6.8, nitrogen: 0.14, bulkDensity: 1.30, cec: 18.0, ocd: 1.9 },
  // Tacoma
  { lat: 47.2529, lon: -122.4443, clay: 25, sand: 35, silt: 40, organicCarbon: 2.2, ph: 6.0, nitrogen: 0.20, bulkDensity: 1.20, cec: 24.0, ocd: 2.8 },
];

/** Shape for a single heatmap demand point. */
export interface DemandPoint {
  lat: number;
  lon: number;
  /** Relative demand weight (0–1) for heatmap intensity. */
  weight: number;
}

/**
 * Demo water demand heatmap points clustered around major Washington
 * metropolitan areas (Seattle, Tacoma, Spokane, and smaller cities).
 */
export const DEMO_DEMAND_POINTS: DemandPoint[] = [
  // Seattle
  { lat: 47.6062, lon: -122.3321, weight: 0.95 },
  { lat: 47.6205, lon: -122.3493, weight: 0.85 },
  { lat: 47.5915, lon: -122.3058, weight: 0.80 },
  // Tacoma
  { lat: 47.2529, lon: -122.4443, weight: 0.70 },
  { lat: 47.2689, lon: -122.4586, weight: 0.60 },
  // Spokane
  { lat: 47.6588, lon: -117.4260, weight: 0.65 },
  { lat: 47.6732, lon: -117.3894, weight: 0.55 },
  // Olympia
  { lat: 47.0379, lon: -122.9007, weight: 0.50 },
  // Bellingham
  { lat: 48.7519, lon: -122.4787, weight: 0.45 },
  // Yakima
  { lat: 46.6021, lon: -120.5059, weight: 0.40 },
  // Wenatchee
  { lat: 47.4235, lon: -120.3103, weight: 0.35 },
  // Walla Walla
  { lat: 46.0646, lon: -118.3430, weight: 0.30 },
];
