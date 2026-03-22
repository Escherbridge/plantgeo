import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";

const bboxSchema = z.string().regex(/^-?\d+\.?\d*,-?\d+\.?\d*,-?\d+\.?\d*,-?\d+\.?\d*$/, 'Invalid bbox format: expected "west,south,east,north"');
import { getNDVITileUrl, getNDWITileUrl } from "@/lib/server/services/vegetation";
import { NLCD_CLASSES, DEGRADED_NLCD_CLASSES } from "@/lib/server/services/nlcd";
import { getStreamflowGauges, getGroundwaterWells } from "@/lib/server/services/usgs-water";
import { getDroughtClassification } from "@/lib/server/services/drought";
import { getWatersheds } from "@/lib/server/services/hydrosheds";
import { getSoilProperties } from "@/lib/server/services/soilgrids";
import { getSoilSurvey } from "@/lib/server/services/usda-soil";
import { getInterventionSuitability } from "@/lib/server/services/carbon-potential";

export const environmentalRouter = router({
  /**
   * Return NDVI tile URL for a given year/month and mode.
   */
  getNDVIMetadata: publicProcedure
    .input(
      z.object({
        year: z.number().int().min(2000).max(2030),
        month: z.number().int().min(1).max(12),
        mode: z.enum(["absolute", "anomaly"]).default("absolute"),
      })
    )
    .query(({ input }) => {
      const tileUrl = getNDVITileUrl(input.year, input.month, input.mode);
      return {
        tileUrl,
        year: input.year,
        month: input.month,
        mode: input.mode,
        attribution: "NASA GIBS / Copernicus Global Land Service",
      };
    }),

  /**
   * Return available vegetation tile sources (NDVI, NDWI).
   */
  getVegetationSources: publicProcedure
    .input(
      z.object({
        year: z.number().int().min(2000).max(2030).default(new Date().getFullYear()),
        month: z.number().int().min(1).max(12).default(new Date().getMonth() + 1),
      })
    )
    .query(({ input }) => {
      return {
        ndvi: {
          tileUrl: getNDVITileUrl(input.year, input.month, "absolute"),
          anomalyTileUrl: getNDVITileUrl(input.year, input.month, "anomaly"),
          attribution: "NASA GIBS",
        },
        ndwi: {
          tileUrl: getNDWITileUrl(input.year, input.month),
          attribution: "NASA GIBS",
        },
        nlcd: {
          wmsUrl:
            "https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms",
          changeWmsUrl:
            "https://www.mrlc.gov/geoserver/mrlc_change/nlcd_2019_2021_change_l48/wms",
          classes: Object.values(NLCD_CLASSES),
        },
      };
    }),

  /**
   * Get reforestation opportunity zones for a bounding box.
   * bbox: "west,south,east,north"
   *
   * TODO: Replace mock polygon generation with a full PostGIS spatial query that
   * intersects degraded NLCD classes, WorldClim precipitation >= 400mm, and
   * soil organic carbon thresholds from the soil health layer (Track 24).
   */
  getReforestationZones: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema,
      })
    )
    .query(({ input }) => {
      const parts = input.bbox.split(",").map(Number);
      if (parts.length !== 4 || parts.some(isNaN)) {
        return { type: "FeatureCollection" as const, features: [] };
      }

      const [west, south, east, north] = parts;
      const midLon = (west + east) / 2;
      const midLat = (south + north) / 2;
      const spanLon = (east - west) / 4;
      const spanLat = (north - south) / 4;

      // Mock opportunity zones derived from bbox centroid — three zones at different
      // suitability levels. A real implementation queries PostGIS for degraded NLCD
      // classes (codes: 52=shrub, 71=grassland, 81=hay) within the bbox and scores
      // each polygon by precipitation, slope, and soil health.
      const zones: GeoJSON.Feature[] = [
        {
          type: "Feature",
          id: "zone-high-1",
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [midLon - spanLon, midLat + spanLat * 0.2],
                [midLon - spanLon * 0.2, midLat + spanLat * 0.2],
                [midLon - spanLon * 0.2, midLat + spanLat],
                [midLon - spanLon, midLat + spanLat],
                [midLon - spanLon, midLat + spanLat * 0.2],
              ],
            ],
          },
          properties: {
            suitability: "High",
            score: 82,
            nlcdClasses: DEGRADED_NLCD_CLASSES,
            factors: [
              "Degraded shrubland (NLCD 52)",
              "Adequate annual rainfall (>600mm)",
              "Low development pressure",
              "Gentle slope (<15%)",
            ],
          },
        },
        {
          type: "Feature",
          id: "zone-medium-1",
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [midLon + spanLon * 0.2, midLat - spanLat * 0.2],
                [midLon + spanLon, midLat - spanLat * 0.2],
                [midLon + spanLon, midLat + spanLat * 0.5],
                [midLon + spanLon * 0.2, midLat + spanLat * 0.5],
                [midLon + spanLon * 0.2, midLat - spanLat * 0.2],
              ],
            ],
          },
          properties: {
            suitability: "Medium",
            score: 61,
            nlcdClasses: [71, 81],
            factors: [
              "Grassland/pasture (NLCD 71/81)",
              "Moderate rainfall (400-600mm)",
              "Moderate slope (15-25%)",
            ],
          },
        },
        {
          type: "Feature",
          id: "zone-low-1",
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [midLon - spanLon * 0.5, midLat - spanLat],
                [midLon + spanLon * 0.5, midLat - spanLat],
                [midLon + spanLon * 0.5, midLat - spanLat * 0.2],
                [midLon - spanLon * 0.5, midLat - spanLat * 0.2],
                [midLon - spanLon * 0.5, midLat - spanLat],
              ],
            ],
          },
          properties: {
            suitability: "Low",
            score: 38,
            nlcdClasses: [81, 82],
            factors: [
              "Cultivated cropland (NLCD 82)",
              "Low annual rainfall (<400mm)",
              "Active agricultural use",
            ],
          },
        },
      ];

      return {
        type: "FeatureCollection" as const,
        features: zones,
      };
    }),

  /**
   * Fetch USGS NWIS streamflow gauges for a bounding box.
   * bbox: "west,south,east,north"
   */
  getStreamflow: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(async ({ input }) => {
      return getStreamflowGauges(input.bbox);
    }),

  /**
   * Fetch current US Drought Monitor classification GeoJSON.
   */
  getDroughtClassification: publicProcedure
    .query(async () => {
      return getDroughtClassification();
    }),

  /**
   * Fetch HUC12 watershed boundaries for a bounding box.
   * bbox: "west,south,east,north"
   */
  getWatersheds: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(async ({ input }) => {
      return getWatersheds(input.bbox);
    }),

  /**
   * Fetch USGS NWIS groundwater well data for a bounding box.
   * bbox: "west,south,east,north"
   */
  getGroundwater: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(async ({ input }) => {
      return getGroundwaterWells(input.bbox);
    }),

  /**
   * Compute a composite water scarcity score for a bounding box.
   * Combines drought class weight + inverse streamflow percentile + groundwater trend penalty.
   * Returns a score 0–100 (100 = most scarce).
   */
  getWaterScarcityScore: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(async ({ input }) => {
      const [gauges, wells, drought] = await Promise.all([
        getStreamflowGauges(input.bbox),
        getGroundwaterWells(input.bbox),
        getDroughtClassification(),
      ]);

      // --- Drought component (0–40 pts) ---
      // USDM category: D0=1 D1=2 D2=3 D3=4 D4=5; None=0
      // Average the DM value across features intersecting the bbox
      const droughtFeatures = drought.features ?? [];
      let droughtSum = 0;
      let droughtCount = 0;
      for (const f of droughtFeatures) {
        const dm = (f.properties as Record<string, unknown>)?.DM as number | undefined;
        if (dm !== undefined && dm !== null) {
          droughtSum += dm;
          droughtCount++;
        }
      }
      const avgDrought = droughtCount > 0 ? droughtSum / droughtCount : 0;
      const droughtScore = Math.min(40, (avgDrought / 4) * 40);

      // --- Streamflow component (0–40 pts) ---
      // Low percentile = high scarcity
      const conditionWeights: Record<string, number> = {
        critically_low: 40,
        low: 30,
        below_normal: 20,
        normal: 10,
        above_normal: 0,
        unknown: 15,
      };
      const streamflowScore =
        gauges.length > 0
          ? gauges.reduce((sum, g) => sum + (conditionWeights[g.condition] ?? 15), 0) /
            gauges.length
          : 15;

      // --- Groundwater component (0–20 pts) ---
      const trendWeights: Record<string, number> = {
        critical: 20,
        declining: 14,
        stable: 6,
        rising: 0,
      };
      const groundwaterScore =
        wells.length > 0
          ? wells.reduce((sum, w) => sum + (trendWeights[w.trend] ?? 6), 0) / wells.length
          : 6;

      const compositeScore = Math.round(droughtScore + streamflowScore + groundwaterScore);

      return {
        score: Math.min(100, compositeScore),
        components: {
          drought: Math.round(droughtScore),
          streamflow: Math.round(streamflowScore),
          groundwater: Math.round(groundwaterScore),
        },
        gaugeCount: gauges.length,
        wellCount: wells.length,
      };
    }),

  /**
   * Get SoilGrids soil properties for a point (lat/lon).
   * Returns pH, organic carbon, nitrogen, bulk density, CEC, OCD.
   * Results cached in Redis for 7 days.
   */
  getSoilProperties: publicProcedure
    .input(z.object({ lat: z.number(), lon: z.number() }))
    .query(async ({ input }) => {
      return getSoilProperties(input.lat, input.lon);
    }),

  /**
   * Get USDA SSURGO soil survey polygon GeoJSON for a bounding box.
   * Returns map unit polygons with drainage class, soil series, hydric flag.
   * bbox: "west,south,east,north"
   */
  getSoilSurvey: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(async ({ input }) => {
      return getSoilSurvey(input.bbox);
    }),

  /**
   * Get intervention suitability scores for a point.
   * Combines SoilGrids soil health + erosion risk + carbon potential
   * for reforestation, silvopasture, cover_cropping, biochar, keyline.
   * Used by Track 26 (Strategy Cards) to rank strategy recommendations.
   */
  getInterventionSuitability: publicProcedure
    .input(z.object({ lat: z.number(), lon: z.number() }))
    .query(async ({ input }) => {
      return getInterventionSuitability(input.lat, input.lon);
    }),
});
