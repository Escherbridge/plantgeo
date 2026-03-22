import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  getRoute,
  getIsochrone,
  getMatrix,
  routeRequestSchema,
  isochroneRequestSchema,
} from "@/lib/server/services/routing";

export const routingRouter = router({
  route: publicProcedure
    .input(routeRequestSchema)
    .mutation(async ({ input }) => {
      return getRoute(input);
    }),

  isochrone: publicProcedure
    .input(isochroneRequestSchema)
    .mutation(async ({ input }) => {
      return getIsochrone(input);
    }),

  matrix: publicProcedure
    .input(
      z.object({
        sources: z.array(
          z.object({ lat: z.number(), lon: z.number() })
        ),
        targets: z.array(
          z.object({ lat: z.number(), lon: z.number() })
        ),
        costing: z.string().default("auto"),
      })
    )
    .mutation(async ({ input }) => {
      return getMatrix(input.sources, input.targets, input.costing);
    }),
});
