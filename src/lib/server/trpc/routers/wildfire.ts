import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { router, publicProcedure, type Context } from "@/lib/server/trpc/init";
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

/**
 * The day the map is drawing, as the warehouse-backed reads here take it. Mirrors
 * `environmental.ts`'s own schema: omitting it means the live edge, not today's date.
 */
const observationDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "Date must be YYYY-MM-DD");

function unpublishedRisk(): never {
  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message: "Validated warehouse-backed wildfire risk output is not published",
  });
}

/** The `geo.layers` row every intervention feature belongs to; mirrors `interventionsRouter`. */
const INTERVENTIONS_LAYER_NAME = "interventions";

/** Resolves the provisioned interventions layer; null when this environment has none. */
async function findInterventionsLayerId(ctx: Context): Promise<string | null> {
  const [layer] = await ctx.db
    .select({ id: layers.id })
    .from(layers)
    .where(eq(layers.name, INTERVENTIONS_LAYER_NAME))
    .limit(1);

  return layer?.id ?? null;
}

export const wildfireRouter = router({
  /**
   * Get fire detection features filtered by bounding box.
   * bbox: "west,south,east,north"
   *
   * `date` narrows to one FIRMS acquisition day instead of the rolling `dayRange` lookback.
   * NOTE: `src/app/api/fires/route.ts` -- which `useFireData` actually calls -- does not
   * forward a date yet, so the map's fire layer is still dateless. See
   * `src/lib/server/AGENTS.md` §slider-day.
   */
  getFireDetections: publicProcedure
    .input(
      z.object({
        bbox: z.string().optional(),
        dayRange: z.number().int().min(1).max(10).default(1),
        date: observationDateSchema.optional(),
      })
    )
    .query(async ({ input }) => {
      return getPublishedFireDetections(input.bbox, input.dayRange, input.date);
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
      // An environment with no interventions layer has no interventions. This public read
      // answered that with an empty collection when it joined `geo.layers` on `name`, and
      // still does: resolving the id must not turn a bare read into a failed request.
      const layerId = await findInterventionsLayerId(ctx);
      if (layerId === null) return [];

      return ctx.db
        .select({
          id: features.id,
          properties: features.properties,
          status: features.status,
          createdAt: features.createdAt,
        })
        .from(features)
        .where(and(eq(features.layerId, layerId), eq(features.status, "published")));
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
   * Read every published, complete warehouse-backed weather observation
   * intersecting a viewport bbox -- the full spread rather than a single
   * nearest-point sample.
   *
   * `date` narrows to that day's newest sample per grid point; omitting it reads the live
   * freshness window unchanged.
   */
  getWeatherForBbox: publicProcedure
    .input(z.object({ bbox: bboxSchema, date: observationDateSchema.optional() }))
    .query(({ input }) => getPublishedWeatherForBbox(input.bbox, input.date)),
});
