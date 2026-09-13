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
import { interventionsRouter } from "@/lib/server/trpc/routers/interventions";
import { interventionSocialRouter } from "@/lib/server/trpc/routers/intervention-social";
import { strategyRouter } from "@/lib/server/trpc/routers/strategy";
import { alertsRouter } from "@/lib/server/trpc/routers/alerts";
import { regionalIntelligenceRouter } from "@/lib/server/trpc/routers/regional-intelligence";
import { forecastsRouter } from "@/lib/server/trpc/routers/forecasts";
import { jobsRouter } from "@/lib/server/trpc/routers/jobs";
import { landContextRouter } from "@/lib/server/trpc/routers/land-context";
import { usersRouter } from "@/lib/server/trpc/routers/users";

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
  // No `community` namespace: its five procedures (submitRequest/voteOnRequest/getRequests/
  // getPriorityZones/getRequestById) were the private strategy-request path, retired by
  // `public_strategy_requests_20260913` Phase 3. Submitting is now
  // `interventions.submitRequest`; reading is the map itself.
  interventions: interventionsRouter,
  interventionSocial: interventionSocialRouter,
  strategy: strategyRouter,
  alerts: alertsRouter,
  regionalIntelligence: regionalIntelligenceRouter,
  forecasts: forecastsRouter,
  jobs: jobsRouter,
  landContext: landContextRouter,
  users: usersRouter,
});

export type AppRouter = typeof appRouter;
