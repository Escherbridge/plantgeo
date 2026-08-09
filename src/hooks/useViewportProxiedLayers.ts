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
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import { viewportBbox } from "@/lib/map/viewport-bbox";
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";
import {
  soilFieldMeasureDefinition,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import type {
  AirTemperatureVariant,
  ClimateFieldSignalId,
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
    }
  );
}

/** Everything that keys a climate read; all of it must match across the two callers. */
export interface ClimateFieldQueryOptions extends ProxiedQueryOptions {
  /** Which quantity to read. */
  signal: ClimateFieldSignalId;
  /** Which daily statistic; only `air-temperature` varies, the rest ignore it server-side. */
  variant: AirTemperatureVariant;
  /** The `climate-field` row's settled day, or undefined at the server's today. */
  date: string | undefined;
}

/**
 * One NASA POWER climate field for the viewport, read from the warehouse.
 *
 * No `zoom`, unlike `useSoilFieldQuery`: the lane has one serving tier, so zoom is not part of
 * the answer and must not be part of the key. Every other input here IS part of the key, so
 * the map and the panel must pass the same three -- both take `bbox` from the one
 * `useViewportBounds()` derivation, `date` from `useDebouncedLayerDay("climate-field")`, and
 * `signal`/`variant` from the climate store.
 */
export function useClimateFieldQuery(
  bbox: string | null | undefined,
  { enabled, signal, variant, date }: ClimateFieldQueryOptions
) {
  const requested = bbox ?? null;
  return trpc.environmental.getClimateField.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, signal, variant, date },
    {
      enabled: enabled && requested !== null && !isWithheld("climate-field"),
      staleTime: CLIMATE_FIELD_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
    }
  );
}
