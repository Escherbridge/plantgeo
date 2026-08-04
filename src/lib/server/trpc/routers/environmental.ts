import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { getInterventionSuitability } from "@/lib/server/services/carbon-potential";
import {
  getMetricAtDate,
  getPublishedDroughtClassification,
  getPublishedGroundwaterWells,
  getPublishedStreamflowGauges,
  getSliderCapabilities,
} from "@/lib/server/services/environmental-read-model";
import { NLCD_CLASSES } from "@/lib/server/services/nlcd";
import {
  getSoilProperties,
  SoilEvidenceUnavailableError,
  SoilUpstreamUnavailableError,
} from "@/lib/server/services/soilgrids";
import {
  GIBS_NDVI_PRODUCT,
  getEnvironmentalTileTemplate,
  getNDVITileUrl,
  getNDWITileUrl,
  resolveGibsNdviDate,
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
        reason: tileUrl
          ? null
          : input.mode === "anomaly"
            ? ("ndvi_anomaly_baseline_not_published" as const)
            : ("gibs_ndvi_coverage_not_available_for_month" as const),
        year: input.year,
        month: input.month,
        mode: input.mode,
        // The composite the tile actually represents -- 8 days ending at this
        // date, not the whole month -- so the client can label it honestly.
        compositeDate: resolveGibsNdviDate(input.year, input.month),
        compositeWindowDays: GIBS_NDVI_PRODUCT.compositeWindowDays,
        maxZoom: GIBS_NDVI_PRODUCT.maxZoom,
        attribution: GIBS_NDVI_PRODUCT.attribution,
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
      const nlcd = getEnvironmentalTileTemplate(
        "land-cover/nlcd-2021/{z}/{x}/{y}.png"
      );
      // Availability is reported per product: NDVI is served by GIBS while
      // NDWI, the NDVI anomaly, and the first-party land-cover release are all
      // genuinely unpublished. A single collapsed flag would either hide the
      // working layer or overstate the missing ones.
      return {
        availability: ndvi ? ("partial" as const) : ("unavailable" as const),
        ndvi: {
          tileUrl: ndvi,
          availability: ndvi ? ("published" as const) : ("unavailable" as const),
          maxZoom: GIBS_NDVI_PRODUCT.maxZoom,
          compositeDate: resolveGibsNdviDate(input.year, input.month),
          compositeWindowDays: GIBS_NDVI_PRODUCT.compositeWindowDays,
          anomalyTileUrl: anomaly,
          anomalyAvailability: anomaly
            ? ("published" as const)
            : ("unavailable" as const),
          anomalyReason: "ndvi_anomaly_baseline_not_published" as const,
          attribution: GIBS_NDVI_PRODUCT.attribution,
        },
        ndwi: {
          tileUrl: ndwi,
          availability: ndwi ? ("published" as const) : ("unavailable" as const),
          reason: "no_public_ndwi_raster_product_exists" as const,
          attribution: null,
        },
        nlcd: {
          tileUrl: nlcd,
          changeTileUrl: getEnvironmentalTileTemplate(
            "land-cover/nlcd-change/{z}/{x}/{y}.png"
          ),
          availability: nlcd ? ("published" as const) : ("unavailable" as const),
          reason: "first_party_land_cover_release_not_published" as const,
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

  /**
   * Serves the newest stored USDM release, clipped and generalized in PostGIS.
   * bbox is optional so the existing no-argument callers keep working; passing
   * one returns far less geometry and is strongly preferred.
   */
  getDroughtClassification: publicProcedure
    .input(z.object({ bbox: bboxSchema.optional() }).optional())
    .query(({ input }) => getPublishedDroughtClassification(input?.bbox)),

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
        // Transient upstream fault: the client may retry, unlike a coverage gap.
        if (error instanceof SoilUpstreamUnavailableError) {
          throw new TRPCError({
            code: "SERVICE_UNAVAILABLE",
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

  /**
   * What the time slider may offer, and the server's UTC today.
   * The client must take "today" from here and never from its own clock, or a
   * browser in another timezone silently disagrees about which days are future.
   */
  getSliderCapabilities: publicProcedure.query(() => getSliderCapabilities()),

  /**
   * One layer's metric for one day. Returns an availability/reason pair rather
   * than a bare empty collection, so "nothing observed that day" is legible as
   * something other than a failure.
   */
  getMetricAtDate: publicProcedure
    .input(
      z.object({
        metric: z.string().trim().min(1).max(64),
        date: z
          .string()
          .regex(/^\d{4}-\d{2}-\d{2}$/, "Date must be YYYY-MM-DD"),
        variant: z.enum(["observed", "monte_carlo", "ml"]).default("observed"),
        bbox: bboxSchema.optional(),
      })
    )
    .query(({ input }) => getMetricAtDate(input)),
});
