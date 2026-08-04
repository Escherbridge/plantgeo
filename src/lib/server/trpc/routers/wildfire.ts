import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { eq, and } from "drizzle-orm";
import {
  getPublishedFireDetections,
  getPublishedWeatherForBbox,
  getPublishedWeatherForPoint,
} from "@/lib/server/services/environmental-read-model";

/** Matches the "west,south,east,north" bbox format environmental.getStreamflow validates. */
const COORDINATE_PATTERN = /^-?(?:\d+(?:\.\d*)?|\.\d+)$/;
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
    if (west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Bounding box is outside WGS84 bounds",
      });
    }
  });

function unpublishedRisk(): never {
  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message: "Validated warehouse-backed wildfire risk output is not published",
  });
}

export const wildfireRouter = router({
  /**
   * Get fire detection features filtered by bounding box.
   * bbox: "west,south,east,north"
   */
  getFireDetections: publicProcedure
    .input(
      z.object({
        bbox: z.string().optional(),
        dayRange: z.number().int().min(1).max(10).default(1),
      })
    )
    .query(async ({ input }) => {
      return getPublishedFireDetections(input.bbox, input.dayRange);
    }),

  /**
   * Calculate fire risk score for a point given terrain + weather parameters.
   */
  getFireRiskForArea: publicProcedure
    .input(
      z.object({
        vegetationType: z.string(),
        slope: z.number().min(0).max(90),
        aspect: z.number().min(0).max(360),
        humidity: z.number().min(0).max(100),
        windSpeed: z.number().min(0),
        lat: z.number().min(-90).max(90).optional(),
        lon: z.number().min(-180).max(180).optional(),
      })
    )
    .query(() => unpublishedRisk()),

  getFireRiskForPoint: publicProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(() => unpublishedRisk()),

  getMTBSPerimeters: publicProcedure
    .input(
      z.object({
        bbox: z.string(),
        yearFrom: z.number().int().min(1984).optional(),
        yearTo: z.number().int().max(2100).optional(),
      })
    )
    .query(() => unpublishedRisk()),

  /**
   * Get intervention features from the interventions layer.
   */
  getInterventions: publicProcedure
    .input(z.object({ teamId: z.string().uuid().optional() }).optional())
    .query(async ({ ctx }) => {
      const rows = await ctx.db
        .select({
          id: features.id,
          properties: features.properties,
          status: features.status,
          createdAt: features.createdAt,
        })
        .from(features)
        .innerJoin(layers, eq(features.layerId, layers.id))
        .where(
          and(
            eq(layers.name, "interventions"),
            eq(features.status, "published")
          )
        );
      return rows;
    }),

  // `createIntervention` was retired here: it wrote unreviewed, unvalidated
  // rows into the shared interventions layer under a fire-specific
  // strategyId/priority vocabulary. Interactive submission now lives in
  // `interventions.submitIntervention`, which validates geometry and always
  // enters expert review. It had no callers, and the layer had no rows, so
  // nothing was migrated.

  /**
   * Read the nearest fresh warehouse-backed weather observation to a point.
   * Never fetches Open-Meteo on request -- the scheduled ingestion job is the
   * only writer, so an empty warehouse reports unavailable instead of stalling
   * the request on an upstream call.
   */
  getWeatherForPoint: publicProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(async ({ input }) => {
      const observation = await getPublishedWeatherForPoint(
        input.lat,
        input.lon
      );
      return observation
        ? { availability: "published" as const, observation }
        : {
            availability: "unavailable" as const,
            reason: "no_fresh_weather_observation_published" as const,
            observation: null,
          };
    }),

  /**
   * Read every published, fresh, complete warehouse-backed weather
   * observation intersecting a viewport bbox -- the full spread rather than
   * a single nearest-point sample.
   */
  getWeatherForBbox: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(({ input }) => getPublishedWeatherForBbox(input.bbox)),
});
