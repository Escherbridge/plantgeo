import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { getInterventionSuitability } from "@/lib/server/services/carbon-potential";
import {
  getPublishedGroundwaterWells,
  getPublishedStreamflowGauges,
} from "@/lib/server/services/environmental-read-model";
import { NLCD_CLASSES } from "@/lib/server/services/nlcd";
import {
  getSoilProperties,
  SoilEvidenceUnavailableError,
} from "@/lib/server/services/soilgrids";
import {
  getEnvironmentalTileTemplate,
  getNDVITileUrl,
  getNDWITileUrl,
} from "@/lib/vegetation";

const COORDINATE_PATTERN = /^-?(?:\d+(?:\.\d*)?|\.\d+)$/;

const pointSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
});

const bboxSchema = z
  .string()
  .trim()
  .min(7)
  .max(100)
  .superRefine((value, context) => {
    const raw = value.split(",");
    if (raw.length !== 4 || raw.some((part) => !COORDINATE_PATTERN.test(part))) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Invalid bbox format: expected "west,south,east,north"',
      });
      return;
    }
    const [west, south, east, north] = raw.map(Number);
    if (west < -180 || east > 180 || south < -90 || north > 90) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Bounding box is outside WGS84 bounds",
      });
    }
    if (west >= east || south >= north) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Bounding box must have positive width and height",
      });
    }
  });

function unavailableCollection(reason: string) {
  return {
    type: "FeatureCollection" as const,
    features: [] as GeoJSON.Feature[],
    availability: "unavailable" as const,
    reason,
    observedAt: null,
    revision: null,
  };
}

export const environmentalRouter = router({
  getNDVIMetadata: publicProcedure
    .input(
      z.object({
        year: z.number().int().min(2000).max(2100),
        month: z.number().int().min(1).max(12),
        mode: z.enum(["absolute", "anomaly"]).default("absolute"),
      })
    )
    .query(({ input }) => {
      const tileUrl = getNDVITileUrl(input.year, input.month, input.mode);
      return {
        tileUrl,
        availability: tileUrl ? ("published" as const) : ("unavailable" as const),
        year: input.year,
        month: input.month,
        mode: input.mode,
        attribution: "NASA GIBS / Copernicus Global Land Service",
      };
    }),

  getVegetationSources: publicProcedure
    .input(
      z.object({
        year: z.number().int().min(2000).max(2100).default(new Date().getFullYear()),
        month: z.number().int().min(1).max(12).default(new Date().getMonth() + 1),
      })
    )
    .query(({ input }) => {
      const ndvi = getNDVITileUrl(input.year, input.month, "absolute");
      const anomaly = getNDVITileUrl(input.year, input.month, "anomaly");
      const ndwi = getNDWITileUrl(input.year, input.month);
      return {
        availability:
          ndvi && anomaly && ndwi
            ? ("published" as const)
            : ("unavailable" as const),
        ndvi: {
          tileUrl: ndvi,
          anomalyTileUrl: anomaly,
          attribution: "NASA GIBS",
        },
        ndwi: { tileUrl: ndwi, attribution: "NASA GIBS" },
        nlcd: {
          tileUrl: getEnvironmentalTileTemplate(
            "land-cover/nlcd-2021/{z}/{x}/{y}.png"
          ),
          changeTileUrl: getEnvironmentalTileTemplate(
            "land-cover/nlcd-change/{z}/{x}/{y}.png"
          ),
          classes: Object.values(NLCD_CLASSES),
        },
      };
    }),

  getReforestationZones: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(() =>
      unavailableCollection("validated_reforestation_output_not_published")
    ),

  getStreamflow: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(({ input }) => getPublishedStreamflowGauges(input.bbox)),

  getDroughtClassification: publicProcedure.query(() =>
    unavailableCollection("bounded_drought_tile_publication_not_available")
  ),

  getWatersheds: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(() => unavailableCollection("watershed_release_not_published")),

  getGroundwater: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(({ input }) => getPublishedGroundwaterWells(input.bbox)),

  getWaterScarcityScore: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(() => ({
      availability: "unavailable" as const,
      reason: "validated_water_scarcity_output_not_published" as const,
      score: null,
      components: null,
      revision: null,
    })),

  getSoilProperties: publicProcedure
    .input(pointSchema)
    .query(async ({ input }) => {
      try {
        return await getSoilProperties(input.lat, input.lon);
      } catch (error) {
        if (error instanceof SoilEvidenceUnavailableError) {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message: error.message,
          });
        }
        throw error;
      }
    }),

  getSoilSurvey: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(() => unavailableCollection("soil_survey_release_not_published")),

  getInterventionSuitability: publicProcedure
    .input(pointSchema)
    .query(({ input }) => getInterventionSuitability(input.lat, input.lon)),
});
