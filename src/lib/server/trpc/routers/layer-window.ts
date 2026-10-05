import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { publicProcedure, publicRateLimit, router } from "@/lib/server/trpc/init";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";
import { LAYER_REGISTRY } from "@/lib/map/layer-registry";
import { acceptedDistributionSignalNames } from "@/lib/layer-window-distribution";
import {
  isAnalysisCalendarDay,
  isLayerWindowPreset,
  isValidLayerWindow,
  isWindowedLayerId,
  layerWindowDayCount,
} from "@/lib/regional-analysis-selection";
import { serverCurrentDate } from "@/lib/server/services/parquet-day";
import {
  LayerWindowDistributionUnavailableError,
  readLayerWindowDistribution,
} from "@/lib/server/services/layer-window-distribution";
import { RegionalEvidenceArgumentError } from "@/lib/server/services/regional-evidence-tools";

const calendarDaySchema = z.string().refine(isAnalysisCalendarDay);

/** Per client per minute: every map Parquet read in agri shares three serving slots. */
export const LAYER_WINDOW_DISTRIBUTION_RATE_LIMIT = 20;

/**
 * Canonical windows only, so the Redis key cannot be defeated by varying the window: real days,
 * a chip preset's length (7/30/90/365 days, both ends counted, as the store builds them), ending
 * no later than the server's UTC today. `signalName` must be one the layer's line can ask for.
 */
export const layerWindowDistributionInput = z
  .object({
    layerId: z.string().max(64).refine(isWindowedLayerId),
    longitude: z.number().min(-180).max(180),
    latitude: z.number().min(-90).max(90),
    rangeStart: calendarDaySchema,
    rangeEnd: calendarDaySchema,
    signalName: z.string().max(64).optional(),
  })
  .strict()
  .refine(isValidLayerWindow)
  .refine((window) => isLayerWindowPreset(layerWindowDayCount(window)), {
    message: "The window must be one of the 7, 30, 90 or 365 day presets",
  })
  .refine((window) => window.rangeEnd <= serverCurrentDate(), {
    message: "The window must not end after today (UTC)",
  })
  .refine(
    (input) =>
      input.signalName === undefined ||
      (isWindowedLayerId(input.layerId) && acceptedDistributionSignalNames(input.layerId).includes(input.signalName)),
    { message: "signalName is not a signal this layer shows" }
  );

export const layerWindowRouter = router({
  /** One dated layer's value distribution over its own window at a point (agri `distribution_at_point`). */
  distributionAtPoint: publicProcedure
    .use(publicRateLimit("layer-window-distribution", LAYER_WINDOW_DISTRIBUTION_RATE_LIMIT))
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
            signalName: input.signalName,
          },
          signal
        );
      } catch (error) {
        if (error instanceof RegionalEvidenceArgumentError) {
          throw new TRPCError({ code: "BAD_REQUEST", message: error.message });
        }
        if (error instanceof LayerWindowDistributionUnavailableError) {
          throw new TRPCError({ code: "SERVICE_UNAVAILABLE", message: error.message });
        }
        rethrowUpstreamFault(error, "The environmental data service");
      }
    }),
});
