import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  getFeatureCountByLayer,
  getRecentActivity,
  getSystemStats,
} from "@/lib/server/services/analytics";
import { layers } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import {
  getRegionalRiskSummary,
  getTrendData,
  getPrioritySubregions,
  getDemandDensity,
} from "@/lib/server/db/analytics";
import { getCachedGeoJSON, cacheGeoJSON } from "@/lib/server/redis";

export const analyticsRouter = router({
  featureCounts: publicProcedure.query(async ({ ctx }) => {
    return getFeatureCountByLayer(ctx.db);
  }),

  recentActivity: publicProcedure
    .input(z.object({ hours: z.number().int().min(1).max(168).default(24) }).optional())
    .query(async ({ ctx, input }) => {
      return getRecentActivity(ctx.db, input?.hours ?? 24);
    }),

  layerStats: publicProcedure.query(async ({ ctx }) => {
    const counts = await getFeatureCountByLayer(ctx.db);
    const layerList = await ctx.db.select().from(layers);
    return counts.map((c) => {
      const layer = layerList.find((l) => l.id === c.layerId);
      return {
        layerId: c.layerId,
        layerName: layer?.name ?? "Unknown",
        count: c.count,
      };
    });
  }),

  systemStats: publicProcedure.query(async ({ ctx }) => {
    return getSystemStats(ctx.db);
  }),

  /**
   * Aggregated risk summary for the current map viewport (bbox).
   * Redis cached for 5 minutes keyed by bbox string.
   */
  getRegionalRiskSummary: publicProcedure
    .input(z.object({ bbox: z.string() }))
    .query(async ({ ctx, input }) => {
      const cacheKey = `analytics:risk:${input.bbox}`;
      const cached = await getCachedGeoJSON<Awaited<ReturnType<typeof getRegionalRiskSummary>>>(
        cacheKey
      ).catch(() => null);
      if (cached) return cached;

      const result = await getRegionalRiskSummary(input.bbox, ctx.db);
      await cacheGeoJSON(cacheKey, result, 300).catch(() => null);
      return result;
    }),

  /**
   * Time-series trend data for the given metric and date range.
   */
  getTrendData: publicProcedure
    .input(
      z.object({
        bbox: z.string(),
        metric: z.enum(["fire", "drought", "ndvi", "water"]),
        days: z.number().int().min(1).max(365).default(30),
      })
    )
    .query(async ({ ctx, input }) => {
      return getTrendData(input.bbox, input.metric, input.days, ctx.db);
    }),

  /**
   * Top 5 highest-priority subregions for the current viewport.
   */
  getPrioritySubregions: publicProcedure
    .input(z.object({ bbox: z.string() }))
    .query(async ({ ctx, input }) => {
      return getPrioritySubregions(input.bbox, ctx.db);
    }),

  /**
   * Community strategy request density as GeoJSON for heatmap rendering.
   */
  getDemandDensity: publicProcedure
    .input(z.object({ bbox: z.string() }))
    .query(async ({ ctx, input }) => {
      const cacheKey = `analytics:demand:${input.bbox}`;
      const cached = await getCachedGeoJSON<GeoJSON.FeatureCollection>(cacheKey).catch(
        () => null
      );
      if (cached) return cached;

      const result = await getDemandDensity(input.bbox, ctx.db);
      await cacheGeoJSON(cacheKey, result, 300).catch(() => null);
      return result;
    }),
});
