import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { protectedProcedure, publicProcedure, router } from "@/lib/server/trpc/init";
import {
  getFeatureCountByLayer,
  getRecentActivity,
  getSystemStats,
} from "@/lib/server/services/analytics";
import { layers } from "@/lib/server/db/schema";
import {
  type PrioritySubregion,
  type RegionalRiskSummary,
  type TrendPoint,
} from "@/lib/server/db/analytics";

function unpublishedAggregate<T>(name: string): T {
  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message: `${name} is unavailable until a versioned warehouse aggregate is published`,
  });
}

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

  /** Reserved contract for a published regional-risk aggregate. */
  getRegionalRiskSummary: publicProcedure
    .input(z.object({ bbox: z.string().max(100) }))
    .query(() => unpublishedAggregate<RegionalRiskSummary>("Regional risk summary")),

  /** Reserved contract for published environmental time series. */
  getTrendData: publicProcedure
    .input(
      z.object({
        bbox: z.string(),
        metric: z.enum(["fire", "drought", "ndvi", "water"]),
        days: z.number().int().min(1).max(365).default(30),
      })
    )
    .query(() => unpublishedAggregate<TrendPoint[]>("Environmental trend series")),

  /** Reserved contract for an approved priority-ranking release. */
  getPrioritySubregions: publicProcedure
    .input(z.object({ bbox: z.string() }))
    .query(() => unpublishedAggregate<PrioritySubregion[]>("Priority-subregion ranking")),

  /** Reserved contract for privacy-reviewed partner opportunity waypoints. */
  getDemandDensity: protectedProcedure
    .input(z.object({ bbox: z.string().max(100) }))
    .query(() =>
      unpublishedAggregate<GeoJSON.FeatureCollection>(
        "Partner opportunity waypoint density"
      )
    ),
});
