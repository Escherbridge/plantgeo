import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { router, publicProcedure, contributorProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { eq, and } from "drizzle-orm";
import {
  getPublishedFireDetections,
  getPublishedWeatherForPoint,
} from "@/lib/server/services/environmental-read-model";

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

  /**
   * Create a new intervention feature in the interventions layer.
   */
  createIntervention: contributorProcedure
    .input(
      z.object({
        strategyId: z.string(),
        name: z.string(),
        description: z.string().optional(),
        priority: z.enum(["High", "Medium", "Low"]).default("Medium"),
        geometry: z.record(z.unknown()).optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      // Find or create the interventions layer
      let layerRow = await ctx.db
        .select({ id: layers.id })
        .from(layers)
        .where(eq(layers.name, "interventions"))
        .limit(1);

      let layerId: string;
      if (layerRow.length === 0) {
        const created = await ctx.db
          .insert(layers)
          .values({
            name: "interventions",
            type: "vector",
            description: "Wildfire intervention strategy zones",
            isPublic: true,
          })
          .returning({ id: layers.id });
        layerId = created[0].id;
      } else {
        layerId = layerRow[0].id;
      }

      const [inserted] = await ctx.db
        .insert(features)
        .values({
          layerId,
          properties: {
            strategyId: input.strategyId,
            name: input.name,
            description: input.description ?? null,
            priority: input.priority,
            geometry: input.geometry ?? null,
          },
        })
        .returning();

      return inserted;
    }),

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
});
