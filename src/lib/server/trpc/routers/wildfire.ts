import { z } from "zod";
import { router, publicProcedure, contributorProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { eq, and, sql } from "drizzle-orm";
import { fetchActiveFiresNASA } from "@/lib/server/services/nasa-firms";
import { calculateFireRisk } from "@/lib/server/services/fire-risk";
import { getCurrentWeather } from "@/lib/server/services/weather";
import { getLandFireEVT } from "@/lib/server/services/landfire";
import { calculateFullFWI } from "@/lib/server/services/fire-weather-index";
import { getMTBSPerimeters } from "@/lib/server/services/mtbs";

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
      return fetchActiveFiresNASA(input.bbox, input.dayRange);
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
    .query(async ({ input }) => {
      const score = calculateFireRisk(input);

      if (input.lat !== undefined && input.lon !== undefined) {
        const weather = await getCurrentWeather(input.lat, input.lon);
        const now = new Date();
        const fwiComponents = calculateFullFWI(
          {
            temp: weather.temperature,
            rh: weather.humidity,
            wind: weather.windSpeed,
            rain: weather.precipitation,
            month: now.getMonth() + 1,
          },
          { ffmc: 85, dmc: 6, dc: 15 }
        );
        return { score, fwiComponents };
      }

      return { score };
    }),

  getFireRiskForPoint: publicProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(async ({ input }) => {
      const [weather, evt] = await Promise.all([
        getCurrentWeather(input.lat, input.lon),
        getLandFireEVT(input.lat, input.lon),
      ]);

      const now = new Date();
      const fwiComponents = calculateFullFWI(
        {
          temp: weather.temperature,
          rh: weather.humidity,
          wind: weather.windSpeed,
          rain: weather.precipitation,
          month: now.getMonth() + 1,
        },
        { ffmc: 85, dmc: 6, dc: 15 }
      );

      const score = calculateFireRisk({
        vegetationType: "mixed_forest",
        slope: 15,
        aspect: 180,
        humidity: weather.humidity,
        windSpeed: weather.windSpeed,
        fuelLoadFactor: evt.fuelParams.fuelLoadFactor,
      });

      const fwiNormalized = Math.min(100, (fwiComponents.fwi / 50) * 100);
      const compositeScore = Math.round(score * 0.6 + fwiNormalized * 0.4);

      const confidence =
        evt.evtCode > 0
          ? fwiComponents.fwi > 30
            ? "high"
            : "medium"
          : "low";

      return {
        score: compositeScore,
        fwiComponents,
        fuelType: evt.evtName,
        evtCode: evt.evtCode,
        confidence,
      };
    }),

  getMTBSPerimeters: publicProcedure
    .input(
      z.object({
        bbox: z.string(),
        yearFrom: z.number().int().min(1984).optional(),
        yearTo: z.number().int().max(2100).optional(),
      })
    )
    .query(async ({ input }) => {
      return getMTBSPerimeters(input.bbox, input.yearFrom, input.yearTo);
    }),

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
   * Fetch current weather for a lat/lon point.
   */
  getWeatherForPoint: publicProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(async ({ input }) => {
      return getCurrentWeather(input.lat, input.lon);
    }),
});
