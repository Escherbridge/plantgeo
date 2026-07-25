import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  getRoute,
  getIsochrone,
  getMatrix,
  routeRequestSchema,
  isochroneRequestSchema,
  matrixRequestSchema,
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
    .input(matrixRequestSchema)
    .mutation(async ({ input }) => {
      return getMatrix(input.sources, input.targets, input.costing);
    }),
});
