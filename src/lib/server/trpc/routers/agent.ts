import { z } from "zod";
import { TRPCError } from "@trpc/server";
import {
  generateRecommendation,
  RecommendationEvidenceUnavailableError,
} from "@/lib/server/services/agent-engine";
import { protectedProcedure, router } from "@/lib/server/trpc/init";

export const agentRouter = router({
  invokeRecommendationAgent: protectedProcedure
    .input(
      z.object({
        lat: z.number().finite().min(-90).max(90),
        lon: z.number().finite().min(-180).max(180),
      })
    )
    .query(async ({ input }: { input: { lat: number; lon: number } }) => {
      try {
        return await generateRecommendation(input.lat, input.lon);
      } catch (error) {
        if (error instanceof RecommendationEvidenceUnavailableError) {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message:
              "Strategy recommendations are unavailable until an approved model release is published.",
          });
        }
        throw error;
      }
    }),
});
