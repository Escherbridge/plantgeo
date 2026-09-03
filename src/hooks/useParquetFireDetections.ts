"use client";

/**
 * The one read of published fire detections. Both callers -- the map's `FireLayer` and the
 * dock's `FireDetails` -- take the day, the bbox and the zoom from inside this hook rather
 * than passing their own, so the two can never key two react-query entries for one answer.
 * See `src/hooks/AGENTS.md` §useParquetFireDetections.
 */

import { useEffect, useMemo, useState } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import { trpc } from "@/lib/trpc/client";
import { useViewportBounds } from "@/hooks/useViewportProxiedLayers";
import { useDebouncedLayerDay } from "@/lib/map/layer-toggle-context";
import {
  fireDetectionTotals,
  presentParquetFireDetections,
  servingZoomTierForMapZoom,
  type FireDetectionCollection,
  type ParquetBrowserFireWindow,
} from "@/lib/environmental/parquet-fire-presentation";
import type { ParquetBrowserReaderResult } from "@/lib/environmental/parquet-presentation";
import { isLayerPermanentlyWithheld } from "@/lib/map/layer-registry";
import type { ZoomTier } from "@/lib/map/zoom-tiers";

/** Placeholder bbox for a viewport that has no bbox; the query is disabled in that case. */
const NO_VIEWPORT_BBOX = "-180,-90,180,90";

/**
 * FIRMS revises the live edge within the day and never revises a settled one, and the key holds
 * the day, so one window covers both: the same quarter hour the other observation feeds use
 * (`useViewportProxiedLayers` §streamflow). This replaces `useFireData`'s two-minute poll --
 * a day partition re-requested every two minutes is load for a byte-identical answer.
 */
const FIRE_DETECTIONS_STALE_TIME_MS = 15 * 60 * 1000;

/** One retry, not react-query's default three; matches every other viewport-keyed read. */
const FIRE_DETECTIONS_RETRY_COUNT = 1;

/**
 * What the surface may say about the read, terminal states included.
 *
 * `pending` and `request_failed` are the two the reader itself cannot report: the first is
 * "no typed answer yet", the second is a transport failure BEFORE any state was returned. The
 * other four are the reader's own, and each is a refusal a caller must render as such --
 * never as a count of zero.
 */
export type ParquetFireReadState =
  | "pending"
  | "request_failed"
  | "ready"
  | "absent"
  | "not_generated"
  | "upstream_unavailable";

export interface ParquetFireDetectionsRead {
  /** The reader's own typed answer, for the evidence/reason/fault a refusal carries. */
  result: ParquetBrowserReaderResult<ParquetBrowserFireWindow> | undefined;
  state: ParquetFireReadState;
  /** Drawable cells. May be a RETAINED frame -- read `isShowingPreviousDay` before captioning. */
  geojson: FireDetectionCollection;
  /** Detections aggregated into the served window; 0 only when `state` is `ready`. */
  detectionCount: number;
  /** Cells in the served window; 0 only when `state` is `ready`. */
  cellCount: number;
  /** The reader hit its row budget: what is drawn is a SUBSET of the viewport. Never hidden. */
  truncated: boolean;
  /** The rung the cells in hand were aggregated at, or null when none could be named. */
  zoomTier: ZoomTier | null;
  /** A request is open for the requested day. */
  isFetching: boolean;
  /** The cells in hand demonstrably answer for `requestedDate`. */
  hasLandedForRequestedDate: boolean;
  /** A retained frame from an earlier key is what is painted. */
  isShowingPreviousDay: boolean;
  /** The fire row's day as SENT: undefined means the server's today, not "no day". */
  requestedDate: string | undefined;
  /** The fire row's day as SETTLED; what a caption may state. */
  settledDate: string | null;
}

/**
 * Published fire detections for the fire row's settled day, the current viewport and the
 * current zoom rung.
 *
 * @param enabled the caller's own gate -- the layer's switch, or the panel section being open.
 *   Never part of the query key, so it cannot split the one entry into two.
 */
export function useParquetFireDetections(enabled: boolean): ParquetFireDetectionsRead {
  const { zoom, bbox } = useViewportBounds();
  const fireDay = useDebouncedLayerDay("fire");
  const requestedZoomTier = servingZoomTierForMapZoom(zoom);

  const query = trpc.wildfire.getFireDetections.useQuery(
    {
      bbox: bbox ?? NO_VIEWPORT_BBOX,
      date: fireDay.requestDate,
      zoom,
      dayRange: 1,
    },
    {
      // A zoom no rung serves is never asked for: the reader would raise on it, which reads as
      // an outage rather than as the upstream bug it is. Same rule as the watershed ceiling.
      //
      // `isLayerPermanentlyWithheld` is governance applied to the REQUEST rather than only to
      // the render, exactly as every proxied lane applies it (`useViewportProxiedLayers.ts`).
      // This hook has two callers, the map layer and the dock panel, so without it an open
      // `FireDetails` would keep requesting a layer the map is forbidden to draw -- a panel as
      // the sole requester of a withheld layer is the case the rule exists for.
      enabled:
        enabled && bbox !== null && requestedZoomTier !== null && !isLayerPermanentlyWithheld("fire"),
      staleTime: FIRE_DETECTIONS_STALE_TIME_MS,
      retry: FIRE_DETECTIONS_RETRY_COUNT,
      placeholderData: keepPreviousData,
    }
  );

  // Annotated, never cast: the browser mirror must stay assignable FROM the procedure's own
  // output, so a drift in the reader's contract fails here instead of being asserted away.
  const result: ParquetBrowserReaderResult<ParquetBrowserFireWindow> | undefined = query.data;
  const isShowingPreviousDay = query.isPlaceholderData === true;
  const hasLandedForRequestedDate =
    query.isSuccess === true &&
    query.isPlaceholderData !== true &&
    query.data !== undefined &&
    result?.state !== "upstream_unavailable";

  // The rung the LAST LANDED answer was served at. A retained frame outlives the zoom it was
  // fetched for, so labelling it with the rung currently being requested would state an
  // aggregation the cells in hand were never aggregated at -- the same misstatement
  // `usePublishedDrawnLayerDays` latches the drawn DAY to avoid. State rather than a ref
  // because the value is read during render; React bails out when it does not change.
  const [landedZoomTier, setLandedZoomTier] = useState<ZoomTier | null>(null);
  useEffect(() => {
    if (!hasLandedForRequestedDate || requestedZoomTier === null) return;
    setLandedZoomTier((current) => (current === requestedZoomTier ? current : requestedZoomTier));
  }, [hasLandedForRequestedDate, requestedZoomTier]);
  const zoomTier = isShowingPreviousDay
    ? (landedZoomTier ?? requestedZoomTier)
    : requestedZoomTier;

  // The latch is NOT handed to the presenter: since 2026-09-02 every served cell declares its own
  // `support.zoomTier`, and a cell's own claim about how it was aggregated is stronger than this
  // hook's bookkeeping about what last landed. What the latch still owns is `zoomTier` below --
  // the one rung a caption may state for the window as a whole, which no single cell can answer.
  const geojson = useMemo(() => presentParquetFireDetections(result), [result]);
  const totals = useMemo(() => fireDetectionTotals(result), [result]);

  const state: ParquetFireReadState =
    result !== undefined
      ? result.state
      : query.isError === true
        ? "request_failed"
        : "pending";

  return {
    result,
    state,
    geojson,
    detectionCount: totals.detectionCount,
    cellCount: totals.cellCount,
    truncated: result?.state === "ready" && result.truncated,
    zoomTier,
    isFetching: query.isFetching === true,
    hasLandedForRequestedDate,
    isShowingPreviousDay,
    requestedDate: fireDay.requestDate,
    settledDate: fireDay.settledDate,
  };
}
