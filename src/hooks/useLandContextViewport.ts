"use client";

/**
 * The automatic viewport read for the land-context reference plane.
 *
 * Owner decision 2026-09-18: land-context GAINS an automatic viewport-bbox query, the third of
 * the three options the 2026-09-14 handoff left open (`conductor/RUNBOOK.md`, "Finding 4").
 * Click-driven point/parcel lookup stays exactly as it was -- this hook is additive and keys its
 * own react-query entry, so `useLandContextQuery`'s point/area entries are untouched.
 *
 * Rationale, the rung table's provenance and the mount snippet: see
 * `src/hooks/AGENTS.md` section land-context-viewport.
 */

import { useMemo } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import {
  resolveZoomTier,
  ZOOM_TIERS,
  ZoomTierResolutionError,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import { bboxSquareDegrees } from "@/lib/map/viewport-bbox";
import { selectFinestAdmittingRung } from "@/lib/map/rung-selection";
import { trpc } from "@/lib/trpc/client";
import { LAND_CONTEXT_GROUP_IDS, type LandContextGroupId } from "@/stores/land-context-store";
import { useViewportBounds, PROXIED_RETRY_COUNT } from "@/hooks/useViewportProxiedLayers";

/**
 * `MAX_AOI_AREA_SQUARE_DEGREES` from `src/lib/server/services/land-context/budgets.ts`, restated
 * for the client rather than imported: that module is server-only. The two must move together.
 *
 * It is the binding constraint on this hook. `readBoundedAoiIntersection` refuses a wider AOI with
 * a typed `budget_exceeded`, so asking anyway would surface a refusal banner on every regional
 * pan; the hook reports `area_over_budget` instead and issues no request at all.
 */
export const LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES = 1;

/**
 * `RUNG_MAX_BBOX_SQUARE_DEGREES` from `src/lib/server/services/land-context/parquet-reader.ts`,
 * restated for the same server-only reason. The server selects the serving rung from the bbox it
 * receives; this copy exists so the client can LABEL which rung it expects and gate on a viewport
 * no rung can answer, never to send a rung of its own -- the procedure takes no zoom, so the map
 * and any panel reading it cannot split into two cache entries.
 */
export const LAND_CONTEXT_RUNG_MAX_BBOX_SQUARE_DEGREES: Readonly<Record<ZoomTier, number>> = {
  13: 4,
  9: 100,
  5: 1_600,
  0: 64_800,
};

/** A reference plane republishes on a release cadence, so a pan back is a local read. */
const LAND_CONTEXT_STALE_TIME_MS = 60 * 60 * 1000;

/**
 * The rung a viewport selects, from zoom AND bbox size together.
 *
 * Both axes, never zoom alone: the finest rung at or below the zoom's own tier that still admits
 * this bbox area. Selecting on zoom alone is precisely the defect the 2026-09-14 handoff confirmed
 * against the botanical plane, where a normal regional viewport at z7-z10 landed on a rung bounded
 * at 100 square degrees and was refused while the rung below it would have answered comfortably.
 *
 * Returns null when no rung admits the viewport -- a statement the caller must surface, not a
 * silently coarsened read.
 */
export function landContextRungForViewport(
  zoom: number,
  areaSquareDegrees: number
): ZoomTier | null {
  let zoomTier: ZoomTier;
  try {
    zoomTier = resolveZoomTier(zoom);
  } catch (error) {
    if (error instanceof ZoomTierResolutionError) return null;
    throw error;
  }
  // The walk is shared with the botanical plane and the server-side reader; see
  // `@/lib/map/rung-selection`. Only the ladder, its ceilings and the zoom gate are local.
  return selectFinestAdmittingRung({
    coarsestFirst: ZOOM_TIERS,
    maxBboxSquareDegrees: LAND_CONTEXT_RUNG_MAX_BBOX_SQUARE_DEGREES,
    areaSquareDegrees,
    finestAllowed: zoomTier,
  });
}

/** Why this hook is or is not asking, in the words a caption may use verbatim. */
export type LandContextViewportState =
  | "no_group_enabled"
  | "viewport_unavailable"
  | "area_over_budget"
  | "no_rung_serves_this_viewport"
  | "reading";

/** The bbox shape `landContext.resolveBoundaryInArea` validates, as this hook derives it. */
export interface LandContextViewportBbox {
  west: number;
  south: number;
  east: number;
  north: number;
}

function parseViewportBbox(bbox: string | null): LandContextViewportBbox | null {
  if (bbox === null) return null;
  const parts = bbox.split(",").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) return null;
  const [west, south, east, north] = parts;
  if (west >= east || south >= north) return null;
  return { west, south, east, north };
}

/**
 * Placeholder bbox for a viewport that cannot be read; the query is disabled in that case, and
 * this value is never sent. Mirrors `NO_VIEWPORT_BBOX` in `useViewportProxiedLayers.ts`.
 */
const NO_VIEWPORT_BBOX: LandContextViewportBbox = {
  west: -125,
  south: 42,
  east: -111,
  north: 49,
};

export interface UseLandContextViewportOptions {
  /** Which land-context groups the user has toggled on; the query is a no-op when none are. */
  enabledGroups: Record<LandContextGroupId, boolean>;
}

export interface UseLandContextViewportResult {
  /** The bbox actually asked for, or null when nothing was asked. */
  bbox: LandContextViewportBbox | null;
  /** The rung this viewport expects to be served from; null when none admits it. */
  expectedZoomTier: ZoomTier | null;
  state: LandContextViewportState;
  query: ReturnType<typeof trpc.landContext.resolveBoundaryInArea.useQuery>;
}

/**
 * Reads land-context boundaries for the CURRENT VIEWPORT, automatically, on every pan and zoom.
 *
 * Retained while panning like every other query the map draws: the polygons in hand are still
 * true where they are. It retains across a pending request and NOT across a failure -- see
 * `KEEP_PREVIOUS_WHILE_PANNING` in `useViewportProxiedLayers.ts` for the full caveat, including
 * why `isLoading` is permanently false after the first success.
 */
export function useLandContextViewport({
  enabledGroups,
}: UseLandContextViewportOptions): UseLandContextViewportResult {
  const { zoom, bbox: viewportBboxString } = useViewportBounds();

  const hasEnabledGroup = useMemo(
    () => LAND_CONTEXT_GROUP_IDS.some((group) => enabledGroups[group]),
    [enabledGroups]
  );

  const resolved = useMemo(() => {
    const bbox = parseViewportBbox(viewportBboxString);
    if (bbox === null) {
      return { bbox: null, expectedZoomTier: null, state: "viewport_unavailable" as const };
    }
    const area = bboxSquareDegrees(viewportBboxString ?? "") ?? Number.POSITIVE_INFINITY;
    const expectedZoomTier = landContextRungForViewport(zoom, area);
    if (expectedZoomTier === null) {
      return { bbox, expectedZoomTier, state: "no_rung_serves_this_viewport" as const };
    }
    if (area > LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES) {
      return { bbox, expectedZoomTier, state: "area_over_budget" as const };
    }
    return { bbox, expectedZoomTier, state: "reading" as const };
  }, [viewportBboxString, zoom]);

  const state: LandContextViewportState = hasEnabledGroup ? resolved.state : "no_group_enabled";
  const isAsking = state === "reading" && resolved.bbox !== null;

  const query = trpc.landContext.resolveBoundaryInArea.useQuery(
    { bbox: resolved.bbox ?? NO_VIEWPORT_BBOX },
    {
      enabled: isAsking,
      staleTime: LAND_CONTEXT_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: keepPreviousData,
    }
  );

  return {
    bbox: isAsking ? resolved.bbox : null,
    expectedZoomTier: resolved.expectedZoomTier,
    state,
    query,
  };
}
