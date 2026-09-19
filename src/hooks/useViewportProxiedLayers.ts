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
 *
 * NO HOOK HERE RETURNS A RAW REACT-QUERY RESULT, and the enforcing half is the TYPE: each of the
 * five is annotated `LiveViewportRead<EnvironmentalAnswers[...]>` (`:144-154`), so returning the
 * observer's result, a spread of it, or its `data` are all assignment errors. The
 * `no-restricted-syntax` ban in `eslint.config.mjs` scoped to this path is a fast second signal
 * and catches only the literal `return <...>.useQuery(...)` shape. See `src/hooks/AGENTS.md`
 * section "Live viewport reads" for what each half does and does not cover.
 */

import { useMemo } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import { isLayerPermanentlyWithheld as isWithheld } from "@/lib/map/layer-registry";
import { bboxSquareDegrees, viewportBbox } from "@/lib/map/viewport-bbox";
import { WORLD_EXTENT_BBOX } from "@/lib/map/world-extent";

/**
 * `MAX_WATERSHED_BBOX_SQUARE_DEGREES` from `src/lib/server/services/hydrosheds.ts`, restated
 * for the client rather than imported: that module is server-only. The two must move together.
 */
export const WATERSHED_LIST_MAX_SQUARE_DEGREES = 1;
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";
// Type-only, so nothing of the drawn-day registry is pulled into this module at runtime.
import type { QueryReadState } from "@/stores/useMetricAtDate";
// Type-only, and the router comes from the CLIENT re-export: `check-client-server-imports.mjs:8-10`
// allows exactly one browser module to name `@/lib/server/trpc/router`, and it is `trpc/client.ts`.
import type { inferRouterOutputs } from "@trpc/server";
import type { AppRouter } from "@/lib/trpc/client";
// The detail floor is the PLANE's own (`DETAIL_ZOOM_FLOOR = 11` server-side), not a rung on the
// `ZOOM_TIERS` ladder; imported rather than restated so the two cannot drift.
import { BOTANICAL_DETAIL_MIN_ZOOM } from "@/lib/botanical-occurrences";
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
const NO_VIEWPORT_BBOX = WORLD_EXTENT_BBOX;

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
 * `ClimateDetails` gate their loading lines on `isFetching` and say so on
 * `isShowingRetainedAnswer` (`LiveViewportRead`, `:174-203`) — which is this flag, gated on the
 * read being live, since react-query leaves `isPlaceholderData` true on a DISABLED observer.
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

/**
 * What each environmental procedure answers, so every read below can NAME what it hands back.
 *
 * This is what makes the no-raw-result guarantee a TYPE rather than a syntax match: with each
 * hook annotated `LiveViewportRead<EnvironmentalAnswers[...]>`, returning the observer's result,
 * a spread of it, or its `data` are all assignment errors, because none of the three has `answer`
 * or `isAnswerLive`. The eslint ban is the fast second signal, not the guarantee -- see
 * `src/hooks/AGENTS.md` section "Live viewport reads" for exactly what it does and does not catch.
 */
type EnvironmentalAnswers = inferRouterOutputs<AppRouter>["environmental"];

/** Caller-side gate. Never part of the cache key, so it cannot split one entry into two. */
export interface ProxiedQueryOptions {
  /** The map layer is mounted, or the panel reading it is open. */
  enabled: boolean;
}

/**
 * A viewport answer together with whether the observer that produced it is LIVE for the request
 * in hand.
 *
 * `KEEP_PREVIOUS_WHILE_PANNING` makes `data` outlive the enablement that fetched it: a disabled
 * react-query observer keeps serving the previous key's answer for as long as the hook is
 * mounted. A consumer therefore cannot read provenance off `data` alone, and the enablement it
 * would have to consult is composed inside the hook -- out of the consumer's reach. Three
 * consecutive style reviews (W8 B3, W9 S1, W10 B1) watched a consumer re-derive that predicate
 * and miss one conjunct each time, so the read reports its own liveness instead and hands the
 * answer back only while it holds. See `src/hooks/AGENTS.md` section "Live viewport reads".
 */
export interface LiveViewportRead<TAnswer> {
  /** The observer's answer, withheld (`undefined`) whenever the read is not live. */
  answer: TAnswer | undefined;
  /** The value this hook passed as the observer's `enabled`, republished verbatim. */
  isAnswerLive: boolean;
  /** The read's transport failure, reported only while the read is live. */
  isError: boolean;
  /** A request is open for the current key. */
  isFetching: boolean;
  /** A first request is open with nothing yet in hand; false forever after under retention. */
  isLoading: boolean;
  /** This request LANDED -- react-query's `isSuccess`, stated positively. */
  isSuccess: boolean;
  /**
   * A retained frame from an earlier request is what is in hand -- react-query's
   * `isPlaceholderData`.
   *
   * Gated like everything else here, which closes a trap of its own: TanStack leaves
   * `isPlaceholderData` TRUE on a DISABLED `keepPreviousData` observer, so an ungated read makes
   * a hidden layer report itself permanently mid-load. `usePublishedDrawnLayerDays` documents
   * that trap and works around it by skipping layers that are not drawn
   * (`src/stores/useMetricAtDate.ts:480-482`).
   *
   * That work-around is doubled for THESE FIVE READS ONLY. It is still the sole guard for every
   * other publisher of that registry -- the fire read, the Parquet lanes in `LayerManager.tsx`,
   * `useLandContextViewport` -- and `useMetricAtDate`'s own query keeps its separate guard at
   * `src/stores/useMetricAtDate.ts:224-231`, which this change does not touch.
   */
  isShowingRetainedAnswer: boolean;
}

/**
 * Publishes a react-query result gated on the enablement composed for its own observer.
 *
 * The single place a retained frame is admitted or withheld. `isAnswerLive` is passed in by the
 * hook that composed `enabled` and is handed to the observer unchanged at the same call site, so
 * a conjunct added to that expression reaches every consumer of the answer by construction rather
 * than by a reviewer noticing the second copy.
 *
 * EVERY field is gated, not only the answer. A flag read off a disabled observer describes the
 * request that observer last ran, which is the same false-provenance claim the answer would make.
 */
function liveViewportRead<TAnswer>(
  isAnswerLive: boolean,
  query: {
    data: TAnswer | undefined;
    isError: boolean;
    isFetching: boolean;
    isLoading: boolean;
    isSuccess: boolean;
    isPlaceholderData: boolean;
  }
): LiveViewportRead<TAnswer> {
  return {
    answer: isAnswerLive ? query.data : undefined,
    isAnswerLive,
    isError: isAnswerLive && query.isError === true,
    isFetching: isAnswerLive && query.isFetching === true,
    isLoading: isAnswerLive && query.isLoading === true,
    isSuccess: isAnswerLive && query.isSuccess === true,
    isShowingRetainedAnswer: isAnswerLive && query.isPlaceholderData === true,
  };
}

/**
 * A live read in the drawn-day registry's own vocabulary, with every field already live-gated.
 *
 * `drawnDayFlagsFromQuery` (`src/stores/useMetricAtDate.ts:459-475`) reads four react-query
 * fields; this is the one translation from a `LiveViewportRead` to them, so no call site
 * reassembles that mapping and none can reach a raw observer to build it from.
 */
export function drawnDayReadState(read: LiveViewportRead<unknown>): QueryReadState {
  return {
    data: read.answer,
    isSuccess: read.isSuccess,
    isFetching: read.isFetching,
    isPlaceholderData: read.isShowingRetainedAnswer,
  };
}

// `isWithheld` is `layer-registry.ts`'s `isLayerPermanentlyWithheld`, imported under the name
// this file's five call sites already use. The rule moved there so the fire lane's own hook
// applies the identical predicate rather than a second copy of it.

/** HUC12 watershed boundaries for the viewport, proxied live from USGS NHD+ HR. */
export function useWatershedsQuery(
  bbox: string | null | undefined,
  { enabled }: ProxiedQueryOptions
): LiveViewportRead<EnvironmentalAnswers["getWatersheds"]> {
  const requested = bbox ?? null;
  // The procedure's own ceiling, mirrored here so a viewport wider than USGS will answer for
  // is never asked. Sending it anyway failed zod validation and surfaced as a request error,
  // which reads as an outage; the map keeps drawing generalized basins from tiles at exactly
  // those zooms, so an outage is precisely what it is not.
  const area = requested === null ? null : bboxSquareDegrees(requested);
  const withinProxyCeiling = area !== null && area <= WATERSHED_LIST_MAX_SQUARE_DEGREES;
  // Composed once, spent twice: the observer's `enabled` and the gate on its answer.
  const isAnswerLive =
    enabled && requested !== null && withinProxyCeiling && !isWithheld("watersheds");
  const query = trpc.environmental.getWatersheds.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX },
    {
      enabled: isAnswerLive,
      staleTime: WATERSHEDS_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
    }
  );
  // This is the one read here NOT configured `KEEP_PREVIOUS_WHILE_PANNING` (see that constant's
  // "deliberately NOT applied" note), so it retains nothing across a key change -- but a
  // disabled observer still serves the CURRENT key's cached entry, and the same shape is worth
  // keeping across all five reads rather than making the reader check which one is which.
  return liveViewportRead(isAnswerLive, query);
}

/**
 * SSURGO map units for the viewport. `environmental.getSoilSurvey` is currently an
 * unconditional stub answering `soil_survey_parquet_lane_not_published`: no lane publishes
 * the survey and nothing proxies USDA Soil Data Access (`usda-soil.ts` no longer exists), so
 * every call returns an empty, unavailable collection until a soil-survey lane ships.
 * `zoom` is still validated and selects render granularity server-side (real map units at
 * high zoom, progressively coarser drainage-class averages below it -- see
 * `src/lib/server/services/zoom-granularity.ts`) and is part of the query key, so the map
 * and the panel must pass the *same* zoom or they split into two cache entries -- both read
 * it from the one `useViewportBounds()` derivation, same as `bbox`. Omitted callers keep
 * the pre-zoom-aware behavior.
 */
export function useSoilSurveyQuery(
  bbox: string | null | undefined,
  { enabled, zoom }: ProxiedQueryOptions & { zoom?: number }
): LiveViewportRead<EnvironmentalAnswers["getSoilSurvey"]> {
  const requested = bbox ?? null;
  // Composed once, spent twice: the observer's `enabled` and the gate on its answer.
  const isAnswerLive = enabled && requested !== null && !isWithheld("soil-survey");
  const query = trpc.environmental.getSoilSurvey.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, zoom },
    {
      enabled: isAnswerLive,
      staleTime: SOIL_SURVEY_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
  return liveViewportRead(isAnswerLive, query);
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
): LiveViewportRead<EnvironmentalAnswers["getSoilField"]> {
  const requested = bbox ?? null;
  const { toggleId } = soilFieldMeasureDefinition(measure);
  // Composed once, spent twice: the observer's `enabled` and the gate on its answer.
  const isAnswerLive = enabled && requested !== null && !isWithheld(toggleId);
  const query = trpc.environmental.getSoilField.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, measure, date, depth, zoom },
    {
      enabled: isAnswerLive,
      staleTime: SOIL_FIELD_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
  return liveViewportRead(isAnswerLive, query);
}

/**
 * Herbarium specimens: the plane is pinned to a `release_set_id` its own `/current` pointer
 * resolves, and a generation never changes once published, so a pan back is a local read. The
 * hour matches the other release-pinned lanes rather than the 15-minute observation feeds; a
 * republish becomes visible through the client's own 120-second `/current` revalidate window
 * plus this stale time, not sooner.
 */
const BOTANICAL_OCCURRENCES_STALE_TIME_MS = 60 * 60 * 1000;

/** The service's own row/cell ceiling (`MAX_LIMIT` in `botanical_occurrences.py`), requested
 * outright rather than left to its 500-row default. */
const BOTANICAL_OCCURRENCES_MAX_LIMIT = 2000;

/** Which of the plane's two answers a given map zoom will get back. */
export type BotanicalBand = "detail" | "aggregate";

/**
 * The band a zoom selects, mirroring the service's own `zoom >= DETAIL_ZOOM_FLOOR` test.
 *
 * Deliberately a bare comparison against a raw integer rather than a `ZoomTier` resolution.
 * The ladder's rungs are 0/5/9/13 and this floor is 11, so rounding a zoom-11 or zoom-12
 * request onto the ladder lands on z9 -- the aggregate rung -- and the map would draw grid
 * cells at the exact zooms a reader asked for specimens. The zoom sent upstream is the raw one
 * for the same reason; the service does its own `int()` coercion.
 */
export function botanicalBandForZoom(zoom: number): BotanicalBand {
  return zoom >= BOTANICAL_DETAIL_MIN_ZOOM ? "detail" : "aggregate";
}

/**
 * Herbarium specimen occurrences for the viewport, at whichever band the zoom selects.
 *
 * ONE query for all three botanical toggles. The plane answers `detail` or `aggregate` from the
 * same route on the same inputs, so splitting it per toggle would key three cache entries for
 * one upstream answer and let the richness and effort layers disagree about the generation they
 * are drawing. `enabled` is therefore "any botanical layer that can draw at this band is on",
 * computed by the caller -- it is a gate, never part of the key, exactly as
 * `ProxiedQueryOptions` documents.
 *
 * `zoom` IS in the key, like `useClimateFieldQuery`'s and unlike `useSoilFieldQuery`'s optional
 * one: it selects which of two shapes comes back, so two zooms are two different answers rather
 * than two aggregations of one.
 *
 * Retained while panning, like every other query the MAP draws: the cells and points in hand are
 * still true where they are. Note the band-switch caveat -- see `useBotanicalOccurrences`'s
 * consumer in `LayerManager`, which reads the RETURNED `state` rather than the requested band, so
 * a retained aggregate frame is never fed to the detail layer during a zoom across the floor.
 *
 * Returns a `LiveViewportRead`, not the react-query result: the retained frame is reachable only
 * through `answer`, which `liveViewportRead` withholds whenever the observer is not live. The
 * raw result is deliberately not exported, so there is nothing for a consumer to re-derive a gate
 * from.
 */
export function useBotanicalOccurrencesQuery(
  bbox: string | null | undefined,
  {
    enabled,
    zoom,
    taxonConceptId,
    family,
    collectionKey,
    eventStart,
    eventEnd,
    spatialQuality,
  }: ProxiedQueryOptions & {
    zoom: number;
    taxonConceptId?: string;
    family?: string;
    collectionKey?: string;
    eventStart?: string;
    eventEnd?: string;
    spatialQuality?: "confirmed" | "possible" | "all";
  }
): LiveViewportRead<EnvironmentalAnswers["getBotanicalOccurrences"]> {
  const requested = bbox ?? null;
  // Composed ONCE, here, and used twice at the same call site: as the observer's `enabled` and as
  // the gate on the answer it hands back. Every conjunct that can disable this read lives in this
  // expression -- the caller's toggle gate, a measurable viewport, and the governance
  // conjunction -- and `requested !== null` is the dynamic one a collapsed or hidden map
  // container trips (`viewportBbox` returns null for a zero-size container,
  // `src/lib/map/viewport-bbox.ts:57-67`).
  const isAnswerLive =
    enabled &&
    requested !== null &&
    // All three toggles share this one read, so the governance gate is the conjunction: the
    // query is withheld only when EVERY botanical row is, which is the same thing as none of
    // them being drawable.
    !(
      isWithheld("botanical-occurrences") &&
      isWithheld("botanical-richness") &&
      isWithheld("botanical-collection-effort")
    );
  const query = trpc.environmental.getBotanicalOccurrences.useQuery(
    {
      bbox: requested ?? NO_VIEWPORT_BBOX,
      zoom,
      taxonConceptId,
      family,
      collectionKey,
      eventStart,
      eventEnd,
      spatialQuality,
      // The service's own ceiling (`MAX_LIMIT` in botanical_occurrences.py), not the 500-row
      // default it falls back to when a caller omits `limit` entirely. The aggregate band's
      // cells are sorted densest-first server-side, so raising this widens how much of a wide
      // viewport's real coverage fits in one page before truncation -- the earlier default left
      // 1500 rows of budget unused on every request.
      limit: BOTANICAL_OCCURRENCES_MAX_LIMIT,
    },
    {
      enabled: isAnswerLive,
      staleTime: BOTANICAL_OCCURRENCES_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
  // The answer type is stated rather than inferred: `query` is react-query's discriminated union
  // over its own states, and inference from a union argument is not worth depending on here.
  return liveViewportRead(isAnswerLive, query);
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
  /**
   * Selects the one physical Parquet rung that answers, and therefore the geometry drawn.
   *
   * `number`, not `number | undefined` as `useSoilFieldQuery` takes it: the procedure requires
   * `zoom`, so an omitted value is a failed request rather than a server-resolved default. Both
   * callers must read it from the SAME `useViewportBounds()` derivation as `bbox`, or the map and
   * the panel key two entries and draw two different aggregations of one viewport.
   */
  zoom: number;
}

/**
 * One NASA POWER climate field for the viewport, read from the warehouse at the rung serving
 * this zoom.
 *
 * `zoom` IS part of the key, correcting the note that stood here: the claim that "the lane has one
 * serving tier" described the reader's hard-coded z13, not the warehouse, which publishes
 * z13/z9/z5/z0 for these lanes like every other. Every input here is part of the key, so the map
 * and the panel must pass the same four -- both take `bbox` and `zoom` from the one
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
  { enabled, signal, variant, date, renderForm, zoom }: ClimateFieldQueryOptions
): LiveViewportRead<EnvironmentalAnswers["getClimateField"]> {
  const requested = bbox ?? null;
  // Composed once, spent twice: the observer's `enabled` and the gate on its answer.
  const isAnswerLive =
    enabled && requested !== null && !isWithheld(climateFieldToggleId(signal));
  const query = trpc.environmental.getClimateField.useQuery(
    { bbox: requested ?? NO_VIEWPORT_BBOX, signal, variant, date, renderForm, zoom },
    {
      enabled: isAnswerLive,
      staleTime: CLIMATE_FIELD_STALE_TIME_MS,
      retry: PROXIED_RETRY_COUNT,
      placeholderData: KEEP_PREVIOUS_WHILE_PANNING,
    }
  );
  return liveViewportRead(isAnswerLive, query);
}
