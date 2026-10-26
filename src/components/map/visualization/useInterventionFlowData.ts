"use client";

import { keepPreviousData } from "@tanstack/react-query";
import { useDebounce } from "@/hooks/useDebounce";
import { useViewportBounds, PROXIED_RETRY_COUNT } from "@/hooks/useViewportProxiedLayers";
import { trpc } from "@/lib/trpc/client";
import { bboxTupleForFlowQuery } from "@/components/map/visualization/intervention-flow-viewport";
import type { InterventionVisualizationMode } from "@/components/map/visualization/intervention-visualization-store";

/**
 * Settles a burst of rapid moveend events (a flyTo's intermediate stops, a fast pinch-pan) into
 * one request -- the same debounce shape `useGeocode` applies to keystrokes. `useViewportBounds`
 * already only changes at moveend/load/resize (see MapView's `publishViewport` wiring), so this
 * debounce is about coalescing a BURST of those, not about throttling continuous pan frames.
 */
const FLOW_VIEWPORT_DEBOUNCE_MS = 400;

/** The interventions layer is the one `RELATIONAL_VISUALIZATION_LAYERS` member today -- see
 * src/lib/server/trpc/routers/visualization.ts. */
const INTERVENTIONS_LAYER_NAME = "interventions";

/** Comfortably under the router's 2_000-row ceiling for one layer's viewport read. */
const FLOW_QUERY_LIMIT = 500;

/** Placeholder input for a disabled query; `enabled` below gates every request, so this is never sent. */
const DISABLED_BBOX: [number, number, number, number] = [-180, -90, 180, 90];

export interface InterventionFlowEdge {
  source: [number, number];
  target: [number, number];
  value: number;
}

export interface UseInterventionFlowDataResult {
  edges: InterventionFlowEdge[];
  isLoading: boolean;
  /**
   * The router caps each read at `limit` with no separate truncation flag -- see its own
   * `.limit(input.limit)` -- so a response landing exactly on the cap is the one signal that
   * more edges may exist outside this viewport read.
   */
  isPossiblyTruncated: boolean;
}

/**
 * Viewport-bbox-driven read for the "flow" visualization mode -- the one mode with no admitted
 * vector-tile data, since no intervention feature carries a `source`/`target` pair today (see
 * `InterventionVisualizationLayer`'s module doc for why heatmap/points need no network read at
 * all). Debounced on top of the already-moveend-only `useViewportBounds` so a burst of rapid
 * moveend events settles into one request rather than one per event.
 */
export function useInterventionFlowData(
  mode: InterventionVisualizationMode
): UseInterventionFlowDataResult {
  const { bbox } = useViewportBounds();
  const debouncedBbox = useDebounce(bbox, FLOW_VIEWPORT_DEBOUNCE_MS);
  const tuple = bboxTupleForFlowQuery(debouncedBbox);
  const isAsking = mode === "flow" && tuple !== null;

  const query = trpc.visualization.getFlowData.useQuery(
    {
      layerName: INTERVENTIONS_LAYER_NAME,
      bbox: tuple ?? DISABLED_BBOX,
      limit: FLOW_QUERY_LIMIT,
    },
    {
      enabled: isAsking,
      staleTime: 60_000,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: keepPreviousData,
    }
  );

  const edges = isAsking ? query.data ?? [] : [];
  return {
    edges,
    isLoading: isAsking && query.isLoading,
    isPossiblyTruncated: isAsking && edges.length >= FLOW_QUERY_LIMIT,
  };
}
