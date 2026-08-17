"use client";

/**
 * The viewport polygon feeds the map AND a panel both read (HUC12 watersheds, SSURGO map
 * units, the ERA5-Land soil fields) as one hook each, so the two issue the *same*
 * react-query entry rather than two that merely look alike. See `src/lib/server/AGENTS.md`
 * §proxied-viewport-queries.
 *
 * Two of the three are proxied from a third party and one is read from the warehouse; what
 * they share is not their upstream but the sharing hazard, which is that a panel describing
 * a layer must never key its read differently from the map drawing it.
 */

import { useMemo } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import { bboxSquareDegrees, viewportBbox } from "@/lib/map/viewport-bbox";

/**
 * `MAX_WATERSHED_BBOX_SQUARE_DEGREES` from `src/lib/server/services/hydrosheds.ts`, restated
 * for the client rather than imported: that module is server-only. The two must move together.
 */
export const WATERSHED_LIST_MAX_SQUARE_DEGREES = 1;
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";
import {
  soilFieldMeasureDefinition,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import {
  climateFieldToggleId,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";

/** Zoom a viewport is read at before the map has reported one of its own. */
const DEFAULT_ZOOM = 8;

/** Placeholder input for a viewport that has no bbox; the query is disabled in that case. */
const NO_VIEWPORT_BBOX = "-180,-90,180,90";

/** HUC12: Redis holds the viewport an hour upstream, so a pan back re-reads rather than re-asks. */
const WATERSHEDS_STALE_TIME_MS = 60 * 60 * 1000;

/**
 * SSURGO: a static survey product, persisted in the warehouse rather than proxied, so a
 * pan back is a local read either way. The long stale time is about not re-asking our own
 * store, not about an upstream cache — see `usda-soil.ts` §soil-survey-persistence.
 */
const SOIL_SURVEY_STALE_TIME_MS = 24 * 60 * 60 * 1000;

/** One retry, not react-query's default three — each attempt re-pays the full upstream cost. */
export const PROXIED_RETRY_COUNT = 1;

/**
 * Hold the previous answer while the next one loads, for the queries the MAP draws.
 *
 * Every query here keys on the viewport, so without this a pan drops the layer to zero features
 * for a full round trip and then refills it — the blank-and-refill that reads as latency and as
 * staleness at once. A retained frame is safe for drawn geometry because geometry is
 * self-locating: the polygons in hand are still true where they are, they merely do not reach
 * the new edge yet.
 *
 * **It retains across a pending request, NOT across a failure.** An errored query has
 * `data: undefined` and `isPlaceholderData: false`, so the layer blanks exactly as it did
 * before — see §retained-answers in `src/components/map/AGENTS.md`.
 *
 * **Every consumer of these hooks owes the reader a label.** `status` reads `"success"` while a
 * placeholder stands in, so `isLoading` is permanently false after the first success: a spinner
 * keyed on it never fires again, and any count or day read off `data` describes the PREVIOUS
 * request. The map publishes the drawn day (`usePublishedDrawnLayerDays`); `SoilDetails` and
 * `ClimateDetails` gate their loading lines on `isFetching` and say so on `isPlaceholderData`.
 *
 * Deliberately NOT applied to `useWatershedsQuery`. Its only consumer is `WaterDetails`, which
 * renders the basins as a LIST under a heading claiming they are the ones in view; a retained
 * list is a false statement about the current viewport rather than an incomplete drawing of it,
 * and there is no caption on that surface to say otherwise. Retaining is permitted, misstating
 * is not — the same rule `useMetricAtDate.resolvedDate` states.
 */
const KEEP_PREVIOUS_WHILE_PANNING = keepPreviousData;

/**
 * ERA5-Land: a reanalysis archive day never changes once published, and the answer is
 * aggregated and contoured per request, so the hour matches vegetation's rather than the
 * 15-minute observation feeds'. The IndexedDB persister backs it beyond the session.
 */
const SOIL_FIELD_STALE_TIME_MS = 60 * 60 * 1000;

/**
 * NASA POWER: an observation archive day never changes once published, and the answer is at
 * most 397 stored cells, so the hour matches the ERA5-Land fields' rather than the 15-minute
 * observation feeds'. The IndexedDB persister backs it beyond the session.
 */
const CLIMATE_FIELD_STALE_TIME_MS = 60 * 60 * 1000;

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
      // Halved because viewportBbox measures out from the centre. Sourced from the real
      // container rather than a constant: the fixed 1024x512 assumption this replaced fetched
      // a rectangle smaller than any modern display, and features stopped dead at its edge.
      bbox: viewportBbox(
        viewport.longitude,
        viewport.latitude,
        zoom,
        viewport.widthPx / 2,
        viewport.heightPx / 2
      ),
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
  // The procedure's own ceiling, mirrored here so a viewport wider than USGS will answer for
  // is never asked. Sending it anyway failed zod validation and surfaced as a request error,
  // which reads as an outage; the map keeps drawing generalized basins from tiles at exactly
  // those zooms, so an outage is precisely what it is not.
  const area = requested === null ? null : bboxSquareDegrees(requested);
  const withinProxyCeiling = area !== null && area <= WATERSHED_LIST_MAX_SQUARE_DEGREES;
  return trpc.environmental.getWatersheds.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX },
    {
      enabled:
        enabled && requested !== null && withinProxyCeiling && !isWithheld("watersheds"),
      staleTime: WATERSHEDS_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
    }
  );
}

/**
 * SSURGO map units for the viewport, read from the warehouse (uncovered ground is warmed
 * from USDA Soil Data Access on first sight).
 * `zoom` selects render granularity server-side (real map units at high zoom,
 * progressively coarser drainage-class averages below it -- see
 * `src/lib/server/services/usda-soil.ts` §soil-survey-zoom) and is part of the query
 * key, so the map and the panel must pass the *same* zoom or they split into two
 * cache entries -- both read it from the one `useViewportBounds()` derivation, same
 * as `bbox`. Omitted callers keep the pre-zoom-aware behavior.
 */
export function useSoilSurveyQuery(
  bbox: string | null | undefined,
  { enabled, zoom }: ProxiedQueryOptions & { zoom?: number }
) {
  const requested = bbox ?? null;
  return trpc.environmental.getSoilSurvey.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, zoom },
    {
      enabled: enabled && requested !== null && !isWithheld("soil-survey"),
      staleTime: SOIL_SURVEY_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
}

/** Everything that keys a soil-moisture read; all of it must match across the two callers. */
export interface SoilFieldQueryOptions extends ProxiedQueryOptions {
  /** Which quantity to read; also selects the toggle whose governance gates the request. */
  measure: SoilFieldMeasure;
  /** The measure's own layer row's settled day, or undefined at the server's today. */
  date: string | undefined;
  depth: SoilFieldDepth;
  /**
   * Selects the server-side aggregation tier; zooming out makes the answer smaller.
   *
   * `number | undefined`, not `number`, and the two callers must agree on which. Coercing a
   * missing zoom to 0 here would mean "coarse tier" while `useSoilSurveyQuery` reads the
   * same absence as "detail" — two different answers for the same viewport, on two cache
   * entries. Undefined is passed through so the server resolves it, once, for both.
   */
  zoom: number | undefined;
}

/**
 * One ERA5-Land soil field for the viewport, read from the warehouse and aggregated
 * server-side by zoom.
 *
 * Every input here is part of the query key, so the map and the panel must pass the same
 * five -- both take `bbox`/`zoom` from the one `useViewportBounds()` derivation, `date` from
 * `useDebouncedLayerDay(<this measure's toggle>)`, and `measure`/`depth` from the soil store.
 * `measure` being in the key is what lets both fields be on at once without sharing an entry.
 *
 * The day must be looked up by the measure's OWN toggle on both sides. Each field is a separate
 * row with a separate slider since 2026-08-09, so a caller that reached for some other layer's
 * day would key a second entry for the same viewport and quietly draw a different date than the
 * one the panel captions.
 */
export function useSoilFieldQuery(
  bbox: string | null | undefined,
  { enabled, measure, date, depth, zoom }: SoilFieldQueryOptions
) {
  const requested = bbox ?? null;
  const { toggleId } = soilFieldMeasureDefinition(measure);
  return trpc.environmental.getSoilField.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, measure, date, depth, zoom },
    {
      enabled: enabled && requested !== null && !isWithheld(toggleId),
      staleTime: SOIL_FIELD_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
}

/** Everything that keys a climate read; all of it must match across the two callers. */
export interface ClimateFieldQueryOptions extends ProxiedQueryOptions {
  /** Which quantity to read. */
  signal: ClimateFieldSignalId;
  /** Which daily statistic; only `air-temperature` varies, the rest ignore it server-side. */
  variant: AirTemperatureVariant;
  /** THIS signal's own row's settled day, or undefined at the server's today. */
  date: string | undefined;
  /**
   * The form to draw it in. Part of the KEY, because it changes the geometry the server
   * returns -- squares, contours or points -- not merely how the same features are painted.
   */
  renderForm: ClimateRenderForm;
}

/**
 * One NASA POWER climate field for the viewport, read from the warehouse.
 *
 * No `zoom`, unlike `useSoilFieldQuery`: the lane has one serving tier, so zoom is not part of
 * the answer and must not be part of the key. Every other input here IS part of the key, so
 * the map and the panel must pass the same three -- both take `bbox` from the one
 * `useViewportBounds()` derivation, `date` from `useDebouncedLayerDay(<this signal's toggle>)`,
 * and `variant` from the climate store.
 *
 * `date` is per SIGNAL since 2026-08-10 and must never be read from a shared climate day: the
 * nine signals have nine rows on nine axes, and passing one row's day to another's read would
 * draw a day that row's slider is not showing -- with its own legend still captioned from the
 * collection's `observedDay`, so the two would visibly disagree.
 */
export function useClimateFieldQuery(
  bbox: string | null | undefined,
  { enabled, signal, variant, date, renderForm }: ClimateFieldQueryOptions
) {
  const requested = bbox ?? null;
  return trpc.environmental.getClimateField.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, signal, variant, date, renderForm },
    {
      enabled:
        enabled && requested !== null && !isWithheld(climateFieldToggleId(signal)),
      staleTime: CLIMATE_FIELD_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
}
