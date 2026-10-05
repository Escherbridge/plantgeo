import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { publicProcedure, router } from "@/lib/server/trpc/init";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";
import { LAYER_REGISTRY } from "@/lib/map/layer-registry";
import {
  isAnalysisCalendarDay,
  isValidLayerWindow,
  isWindowedLayerId,
} from "@/lib/regional-analysis-selection";
import { readLayerWindowDistribution } from "@/lib/server/services/layer-window-distribution";
import { RegionalEvidenceArgumentError } from "@/lib/server/services/regional-evidence-tools";

const calendarDaySchema = z.string().refine(isAnalysisCalendarDay);

/** The same window rule the analysis route enforces: real days, end ≥ start, ≤ 366 days. */
export const layerWindowDistributionInput = z
  .object({
    layerId: z.string().max(64).refine(isWindowedLayerId),
    longitude: z.number().min(-180).max(180),
    latitude: z.number().min(-90).max(90),
    rangeStart: calendarDaySchema,
    rangeEnd: calendarDaySchema,
  })
  .strict()
  .refine(isValidLayerWindow);

export const layerWindowRouter = router({
  /** One dated layer's value distribution over its own window at a point (agri `distribution_at_point`). */
  distributionAtPoint: publicProcedure
    .input(layerWindowDistributionInput)
    .query(async ({ input, signal }) => {
      if (!isWindowedLayerId(input.layerId)) throw new TRPCError({ code: "BAD_REQUEST" });
      const surfaceName = LAYER_REGISTRY[input.layerId].warehouseLayerName!;
      try {
        return await readLayerWindowDistribution(
          {
            surfaceName,
            longitude: input.longitude,
            latitude: input.latitude,
            rangeStart: input.rangeStart,
            rangeEnd: input.rangeEnd,
          },
          signal
        );
      } catch (error) {
        if (error instanceof RegionalEvidenceArgumentError) {
          throw new TRPCError({ code: "BAD_REQUEST", message: error.message });
        }
        rethrowUpstreamFault(error, "The environmental data service");
      }
    }),
});
