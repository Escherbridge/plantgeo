"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  BOTANICAL_OCCURRENCES_PROXY_PATH,
  botanicalProxyAnswerSchema,
  botanicalProxyErrorSchema,
  type BotanicalProxyAnswer,
  type BotanicalProxyError,
} from "@/lib/environmental/botanical-proxy-contract";
import {
  BOTANICAL_MAX_BBOX_SQUARE_DEGREES,
  BOTANICAL_OCCURRENCE_MAX_LIMIT,
  botanicalServingBandForViewport,
  botanicalSupportBandForZoom,
  type BotanicalSpatialQuality,
  type BotanicalSupportBand,
} from "@/lib/botanical-occurrences";
import type { RungSelectionResult } from "@/lib/map/rung-selection";

/**
 * Viewport-scoped read of the botanical-occurrences plane through the Next.js proxy route.
 *
 * WHY A PLAIN-FETCH HOOK BESIDE `useBotanicalOccurrencesQuery`. The react-query hook in
 * `useViewportProxiedLayers.ts` is the right shape for a surface that shares one cache entry
 * between a map layer and a panel describing it. This one is the standalone lane: one abortable
 * request per viewport, no cache to key, and the semantic states a layer component needs to caption
 * itself. Both go through the SAME server client, so they cannot disagree about what is published.
 *
 * SUPERSEDED ANSWERS ARE DISCARDED TWICE. The in-flight request is aborted when its inputs change,
 * AND every response is checked against a monotonic request sequence before it is allowed to become
 * state. Abort alone is not enough: a response can already be in the microtask queue when the abort
 * lands, and applying it would draw a viewport the reader has already panned away from -- the same
 * out-of-order hazard `useViewportProxiedLayers`'s query keys avoid structurally.
 *
 * THE ANSWER IS VALIDATED AT THIS BOUNDARY. The route is ours, but its body is still JSON crossing
 * a process edge: it is parsed by `botanicalProxyAnswerSchema` before any component sees it, so a
 * version skew between a deployed route and a cached page surfaces as `error`, not as a layer
 * drawing `undefined`.
 *
 * STALE IS REPORTED, NEVER HIDDEN. While a new viewport loads, the previous answer is retained and
 * `isStale` is true; on a FAILURE it is dropped, because a retained collection under an error
 * caption is a false statement about the current viewport rather than an incomplete drawing of it.
 */

/** Which semantic state a consumer is in. `empty` is a real answer; `error` is the absence of one. */
export type BotanicalOccurrencesPhase = "idle" | "loading" | "success" | "empty" | "error";

/** Everything a layer or panel needs to draw itself honestly, including why it cannot. */
export interface BotanicalOccurrencesSnapshot {
  phase: BotanicalOccurrencesPhase;
  /** The plane's decoded answer, or null when there is none to show. */
  answer: BotanicalProxyAnswer | null;
  /** The route's stable error shape; `reason` carries the plane's own refusal or pointer failure. */
  error: BotanicalProxyError | null;
  /** A previous viewport's answer is on screen while the current one loads. */
  isStale: boolean;
  /** The plane bounded the answer: more rows match than were returned (`truncated`). */
  isPartial: boolean;
  /** Which band this answer came from, so a caption never claims specimens over grid cells. */
  band: ReturnType<typeof botanicalSupportBandForZoom>;
  /**
   * The rung that actually answered, which is NOT always `band`: a viewport wider than its zoom's
   * own rung admits is served from the next rung out (owner decision 2026-09-18). The route states
   * it in `servingRung`; before an answer lands this is the hook's own selection, and it is null
   * only when no rung admits the viewport at all.
   */
  servingBand: BotanicalSupportBand | null;
}

/** The viewport and filters one read is keyed on. A null bbox disables the read entirely. */
export interface UseBotanicalOccurrencesOptions {
  /** `"west,south,east,north"`, or null when the viewport is not expressible as one. */
  bbox: string | null;
  zoom: number;
  /** Caller-side gate: at least one botanical layer is on, or a panel reading it is open. */
  enabled: boolean;
  taxonConceptId?: string;
  family?: string;
  collectionKey?: string;
  eventStart?: string;
  eventEnd?: string;
  spatialQuality?: BotanicalSpatialQuality;
  limit?: number;
}

const IDLE: BotanicalOccurrencesSnapshot = {
  phase: "idle",
  answer: null,
  error: null,
  isStale: false,
  isPartial: false,
  band: "grid-0.25",
  servingBand: "grid-0.25",
};

/**
 * The rung this viewport selects, as a discriminated result (S8, W3 review) -- the ONE refusal
 * still checked without spending a round trip. The route runs the same selection over the same
 * table, so this copy can only agree with it or be wrong, never be stricter.
 */
function servingBandForViewport(bbox: string, zoom: number): RungSelectionResult<BotanicalSupportBand> {
  const [west, south, east, north] = bbox.split(",").map((part) => Number(part));
  if (![west, south, east, north].every(Number.isFinite)) {
    return { kind: "selected", rung: botanicalSupportBandForZoom(zoom) };
  }
  return botanicalServingBandForViewport(zoom, (east - west) * (north - south));
}

function buildRequestUrl(options: UseBotanicalOccurrencesOptions, bbox: string): string {
  const search = new URLSearchParams({ bbox, zoom: String(Math.trunc(options.zoom)) });
  if (options.taxonConceptId) search.set("taxonConceptId", options.taxonConceptId);
  if (options.family) search.set("family", options.family);
  if (options.collectionKey) search.set("collectionKey", options.collectionKey);
  if (options.eventStart) search.set("eventStart", options.eventStart);
  if (options.eventEnd) search.set("eventEnd", options.eventEnd);
  if (options.spatialQuality) search.set("spatialQuality", options.spatialQuality);
  if (options.limit !== undefined) {
    search.set("limit", String(Math.min(options.limit, BOTANICAL_OCCURRENCE_MAX_LIMIT)));
  }
  return `${BOTANICAL_OCCURRENCES_PROXY_PATH}?${search.toString()}`;
}

export function useBotanicalOccurrences(
  options: UseBotanicalOccurrencesOptions
): BotanicalOccurrencesSnapshot {
  const [snapshot, setSnapshot] = useState<BotanicalOccurrencesSnapshot>(IDLE);

  // The whole request as one string, so the effect re-runs on a real input change and not on a
  // parent re-render that rebuilt an equivalent options object.
  const requestUrl = useMemo(
    // The listed fields ARE the dependency; depending on `options` itself would rebuild the URL
    // on every parent render and re-fetch a viewport that did not move.
    () => (options.bbox === null ? null : buildRequestUrl(options, options.bbox)),
    [
      options.bbox,
      options.zoom,
      options.taxonConceptId,
      options.family,
      options.collectionKey,
      options.eventStart,
      options.eventEnd,
      options.spatialQuality,
      options.limit,
    ]
  );

  const band = botanicalSupportBandForZoom(options.zoom);
  // The rung this viewport will be served from, which the route re-derives identically.
  const rungSelection: RungSelectionResult<BotanicalSupportBand> =
    options.bbox === null
      ? { kind: "selected", rung: band }
      : servingBandForViewport(options.bbox, options.zoom);
  const selectedBand = rungSelection.kind === "selected" ? rungSelection.rung : null;
  const latestRequest = useRef(0);

  useEffect(() => {
    if (!options.enabled || requestUrl === null) {
      setSnapshot(IDLE);
      return;
    }
    if (rungSelection.kind !== "selected") {
      // S8, W3 review: the two refusal kinds get distinct detail text now, rather than both
      // reading as "the viewport is too wide" -- `rung_not_on_ladder` is a configuration defect
      // (the zoom-selected band fell off the ladder), never a viewport the user could narrow.
      const detail =
        rungSelection.kind === "no_rung_admits_area"
          ? `no published rung answers a bbox wider than ${BOTANICAL_MAX_BBOX_SQUARE_DEGREES} square degrees`
          : `zoom ${options.zoom} selects band "${rungSelection.rung}", which is not on the published ladder`;
      setSnapshot({
        phase: "error",
        answer: null,
        error: {
          error: "The viewport is wider than every published rung",
          reason: "bbox_too_large_for_zoom",
          detail,
        },
        isStale: false,
        isPartial: false,
        band,
        servingBand: null,
      });
      return;
    }

    const sequence = latestRequest.current + 1;
    latestRequest.current = sequence;
    const controller = new AbortController();

    setSnapshot((previous) => ({
      ...previous,
      phase: "loading",
      error: null,
      isStale: previous.answer !== null,
      band,
      servingBand: selectedBand,
    }));

    void (async () => {
      try {
        const response = await fetch(requestUrl, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        const payload: unknown = await response.json();
        if (latestRequest.current !== sequence) return;

        if (!response.ok) {
          const parsedError = botanicalProxyErrorSchema.safeParse(payload);
          setSnapshot({
            phase: "error",
            answer: null,
            error: parsedError.success
              ? parsedError.data
              : { error: "The botanical-occurrences plane answered unexpectedly", reason: "unrecognized_error" },
            isStale: false,
            isPartial: false,
            band,
            servingBand: selectedBand,
          });
          return;
        }

        const parsed = botanicalProxyAnswerSchema.safeParse(payload);
        if (!parsed.success) {
          setSnapshot({
            phase: "error",
            answer: null,
            error: {
              error: "The botanical-occurrences answer did not match its published shape",
              reason: "contract_mismatch",
            },
            isStale: false,
            isPartial: false,
            band,
            servingBand: selectedBand,
          });
          return;
        }

        const answer = parsed.data;
        const returned = answer.state === "detail" ? answer.features.length : answer.cells.length;
        setSnapshot({
          // `empty` is a POSITIVE answer -- this generation holds nothing here -- and is kept
          // distinct from `error` so a caption can say which of the two happened.
          phase: returned === 0 ? "empty" : "success",
          answer,
          error: null,
          isStale: false,
          isPartial: answer.truncated,
          band,
          // The ANSWER's own rung, never the request's guess: the route is the authority on which
          // rung answered, and a caption that disagreed with it would misname the evidence.
          servingBand: answer.servingRung,
        });
      } catch (error) {
        if (controller.signal.aborted || latestRequest.current !== sequence) return;
        setSnapshot({
          phase: "error",
          answer: null,
          error: {
            error: "The botanical-occurrences plane could not be reached",
            reason: "request_failed",
            detail: error instanceof Error ? error.message : undefined,
          },
          isStale: false,
          isPartial: false,
          band,
          servingBand: selectedBand,
        });
      }
    })();

    // `requestUrl` already encodes every request-shaping field, and both `band` and `selectedBand`
    // are derived from the zoom and bbox inside it, so those two are the whole dependency here.
    return () => controller.abort();
  }, [requestUrl, options.enabled]);

  return snapshot;
}
