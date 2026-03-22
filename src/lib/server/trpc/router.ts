import { router } from "@/lib/server/trpc/init";
import { layersRouter } from "@/lib/server/trpc/routers/layers";
import { routingRouter } from "@/lib/server/trpc/routers/routing";
import { teamsRouter } from "@/lib/server/trpc/routers/teams";
import { contributionsRouter } from "@/lib/server/trpc/routers/contributions";
import { analyticsRouter } from "@/lib/server/trpc/routers/analytics";
import { trackingRouter } from "@/lib/server/trpc/routers/tracking";
import { wildfireRouter } from "@/lib/server/trpc/routers/wildfire";
import { visualizationRouter } from "@/lib/server/trpc/routers/visualization";
import { placesRouter } from "@/lib/server/trpc/routers/places";
import { environmentalRouter } from "@/lib/server/trpc/routers/environmental";
import { communityRouter } from "@/lib/server/trpc/routers/community";
import { strategyRouter } from "@/lib/server/trpc/routers/strategy";
import { alertsRouter } from "@/lib/server/trpc/routers/alerts";
import { regionalIntelligenceRouter } from "@/lib/server/trpc/routers/regional-intelligence";

export const appRouter = router({
  layers: layersRouter,
  routing: routingRouter,
  teams: teamsRouter,
  contributions: contributionsRouter,
  analytics: analyticsRouter,
  tracking: trackingRouter,
  wildfire: wildfireRouter,
  visualization: visualizationRouter,
  places: placesRouter,
  environmental: environmentalRouter,
  community: communityRouter,
  strategy: strategyRouter,
  alerts: alertsRouter,
  regionalIntelligence: regionalIntelligenceRouter,
});

export type AppRouter = typeof appRouter;
