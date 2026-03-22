import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { getStrategyRecommendations } from "@/lib/server/services/strategy-scoring";
import { getStrategySuppliers } from "@/lib/server/services/plantcommerce-api";

export const strategyRouter = router({
  /**
   * Get ranked strategy recommendations for a lat/lon location.
   * Fetches environmental indicators and scores all 6 strategy types.
   */
  getStrategyRecommendations: publicProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(async ({ input }) => {
      return getStrategyRecommendations(input.lat, input.lon);
    }),

  /**
   * Get PlantCommerce suppliers for a strategy type at a location.
   * Returns empty array when PlantCommerce API is not configured.
   */
  getStrategySuppliers: publicProcedure
    .input(
      z.object({
        strategyId: z.string(),
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(async ({ input }) => {
      return getStrategySuppliers(input.strategyId, input.lat, input.lon);
    }),
});
