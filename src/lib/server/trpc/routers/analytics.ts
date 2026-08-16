import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { protectedProcedure, publicProcedure, router } from "@/lib/server/trpc/init";
import {
  getFeatureCountByLayer,
  getLayerFeatureStats,
  getRecentActivity,
  getSystemStats,
} from "@/lib/server/services/analytics";
import {
  type PrioritySubregion,
  type RegionalRiskSummary,
  type TrendPoint,
} from "@/lib/server/db/analytics";
import { parseBoundingBox } from "@/lib/server/security/bbox";
import {
  activityGridToFeatureCollection,
  aggregateActivityGrid,
} from "@/lib/server/services/community-activity";

const DEMAND_GRID_ZOOM = 6;
const DEMAND_GRID_LIMIT = 2_000;

export const analyticsRouter = router({
  featureCounts: publicProcedure.query(async ({ ctx }) => {
    return getFeatureCountByLayer(ctx.db);
  }),

  recentActivity: publicProcedure
    .input(z.object({ hours: z.number().int().min(1).max(168).default(24) }).optional())
    .query(async ({ ctx, input }) => {
      return getRecentActivity(ctx.db, input?.hours ?? 24);
    }),

  /**
   * One read, not two. This used to count all 4.97M published features and then
   * `SELECT * FROM layers` -- every column of every layer, style jsonb included -- purely to
   * map an id to a name it then matched in JavaScript. `geo.mv_layer_feature_stats` carries
   * both, one row per layer. The returned shape is unchanged.
   */
  layerStats: publicProcedure.query(async ({ ctx }) => {
    return getLayerFeatureStats(ctx.db);
  }),

  systemStats: publicProcedure.query(async ({ ctx }) => {
    return getSystemStats(ctx.db);
  }),

  /**
   * Still closed: every field of RegionalRiskSummary is a non-nullable number or
   * trend, and no published risk aggregate exists to fill them. Returning zeros
   * would fabricate a confident "no risk" answer — see src/lib/server/db/analytics.ts.
   */
  getRegionalRiskSummary: publicProcedure
    .input(z.object({ bbox: z.string().max(100) }))
    .query((): RegionalRiskSummary => {
      throw new TRPCError({
        code: "NOT_FOUND",
        message:
          "No regional risk aggregate is published. The predictive tables this reads " +
          "(agri.danger_prediction, agri.strategy_effect) do not exist in any schema yet.",
      });
    }),

  /**
   * Open and empty: no environmental time-series aggregate is published yet, so the
   * honest series is no points. It fills in as soon as one is.
   */
  getTrendData: publicProcedure
    .input(
      z.object({
        bbox: z.string(),
        metric: z.enum(["fire", "drought", "ndvi", "water"]),
        days: z.number().int().min(1).max(365).default(30),
      })
    )
    .query((): TrendPoint[] => []),

  /** Open and empty until a ranked-subregion release exists to read. */
  getPrioritySubregions: publicProcedure
    .input(z.object({ bbox: z.string() }))
    .query((): PrioritySubregion[] => []),

  /** Community request density, aggregated server-side onto a coarse grid. */
  getDemandDensity: protectedProcedure
    .input(z.object({ bbox: z.string().max(100) }))
    .query(async ({ ctx, input }) => {
      const boundingBox = parseBoundingBox(input.bbox);
      if (!boundingBox) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "Invalid bbox. Expected ordered west,south,east,north",
        });
      }
      const grid = await aggregateActivityGrid(ctx.db, {
        boundingBox,
        zoom: DEMAND_GRID_ZOOM,
        limit: DEMAND_GRID_LIMIT,
        minimumVotes: 0,
        minimumFeatureCount: 1,
      });
      return activityGridToFeatureCollection(grid);
    }),
});
