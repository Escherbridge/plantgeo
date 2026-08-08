import { router, publicProcedure } from "@/lib/server/trpc/init";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";
import {
  forecastSeriesRequestSchema,
  getPublishedForecastSeries,
} from "@/lib/server/services/agri-forecasts";

export const forecastsRouter = router({
  getSeries: publicProcedure.input(forecastSeriesRequestSchema).query(async ({ input }) => {
    try {
      return await getPublishedForecastSeries(input);
    } catch (error) {
      rethrowUpstreamFault(error, "The forecast service");
    }
  }),
});
