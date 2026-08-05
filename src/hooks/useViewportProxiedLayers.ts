"use client";

/**
 * The two viewport-proxied polygon feeds (HUC12 watersheds, SSURGO map units) as one
 * hook each, so the map and the panels that describe it issue the *same* react-query
 * entry rather than two that merely look alike. See `src/lib/server/AGENTS.md`
 * §proxied-viewport-queries.
 */

import { useMemo } from "react";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import { viewportBbox } from "@/lib/map/viewport-bbox";
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";

/** Zoom a viewport is read at before the map has reported one of its own. */
const DEFAULT_ZOOM = 8;

/** Placeholder input for a viewport that has no bbox; the query is disabled in that case. */
const NO_VIEWPORT_BBOX = "-180,-90,180,90";

/** HUC12: Redis holds the viewport an hour upstream, so a pan back re-reads rather than re-asks. */
const WATERSHEDS_STALE_TIME_MS = 60 * 60 * 1000;

/** SSURGO: republished on an annual cycle and cached a day upstream. */
const SOIL_SURVEY_STALE_TIME_MS = 24 * 60 * 60 * 1000;

/** One retry, not react-query's default three — each attempt re-pays the full upstream cost. */
const PROXIED_RETRY_COUNT = 1;

/** The viewport as every viewport-scoped query keys on it. */
export interface ViewportBounds {
  zoom: number;
  /** "west,south,east,north", or null when the viewport is not expressible as one. */
  bbox: string | null;
}

/** The one derivation of the viewport bbox; a second copy would key a second query entry. */
export function useViewportBounds(): ViewportBounds {
  const viewport = useMapStore((state) => state.viewport);
  return useMemo(() => {
    const zoom = viewport.zoom ?? DEFAULT_ZOOM;
    return {
      zoom,
      bbox: viewportBbox(viewport.longitude, viewport.latitude, zoom),
    };
  }, [viewport]);
}

/** Caller-side gate. Never part of the cache key, so it cannot split one entry into two. */
export interface ProxiedQueryOptions {
  /** The map layer is mounted, or the panel reading it is open. */
  enabled: boolean;
}

/**
 * Governance, applied to the request and not just to the render: a layer the registry
 * withholds at every date is never asked for, so a panel can never become the sole
 * requester of a layer the map is forbidden to draw.
 */
function isWithheld(toggleId: LayerToggleId): boolean {
  return LAYER_REGISTRY[toggleId].permanentlyUnavailableReason !== null;
}

/** HUC12 watershed boundaries for the viewport, proxied live from USGS NHD+ HR. */
export function useWatershedsQuery(
  bbox: string | null | undefined,
  { enabled }: ProxiedQueryOptions
) {
  const requested = bbox ?? null;
  return trpc.environmental.getWatersheds.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX },
    {
      enabled: enabled && requested !== null && !isWithheld("watersheds"),
      staleTime: WATERSHEDS_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
    }
  );
}

/** SSURGO map units for the viewport, proxied live from USDA Soil Data Access. */
export function useSoilSurveyQuery(
  bbox: string | null | undefined,
  { enabled }: ProxiedQueryOptions
) {
  const requested = bbox ?? null;
  return trpc.environmental.getSoilSurvey.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX },
    {
      enabled: enabled && requested !== null && !isWithheld("soil-survey"),
      staleTime: SOIL_SURVEY_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
    }
  );
}
