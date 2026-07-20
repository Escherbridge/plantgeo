import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { protectedProcedure, router } from "@/lib/server/trpc/init";
import {
  getStrategyRecommendations,
  StrategyEvidenceUnavailableError,
} from "@/lib/server/services/strategy-scoring";

function supplierDirectoryUnavailable(): never {
  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message:
      "Partner supplier matching is inactive until reviewed directory data, entitlement, and outbound-location consent are available",
  });
}

export const strategyRouter = router({
  /** Return recommendations only when validated evidence is published. */
  getStrategyRecommendations: protectedProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(async ({ input }) => {
      try {
        return await getStrategyRecommendations(input.lat, input.lon);
      } catch (error) {
        if (error instanceof StrategyEvidenceUnavailableError) {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message: error.message,
            cause: error,
          });
        }
        throw error;
      }
    }),

  /** Keeps raw coordinates inside PlantGeo until a reviewed partner contract exists. */
  getStrategySuppliers: protectedProcedure
    .input(
      z.object({
        strategyId: z.string(),
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .query(() => supplierDirectoryUnavailable()),
});
