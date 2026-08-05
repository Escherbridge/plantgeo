"use client";

import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useDebounce } from "@/hooks/useDebounce";
import { getVanillaTrpcClient } from "@/lib/trpc/client";
import type {
  MetricAtDateAvailability,
  MetricAtDateCollection,
  MetricAtDateInput,
  MetricVariant,
} from "@/types/time-slider";
import {
  addDays,
  describeAvailability,
  findLayerCapability,
  layerAvailabilityAt,
  resolveVariant,
  useTimeSliderStore,
} from "./time-slider-store";

/**
 * Milliseconds of stillness before a scrub turns into a request.
 *
 * Exported and shared rather than restated per caller: the slider writes a new day on every
 * pointer tick, and every consumer that turns the day into a request must settle on the SAME
 * boundary. Two different settle windows would issue two waves of requests per scrub for the
 * same day -- see `useDebouncedMapDay` in `src/lib/map/layer-toggle-context.ts`.
 */
export const SCRUB_SETTLE_MS = 250;
/**
 * Days either side of the selection that are worth warming.
 *
 * ONE, not seven. Every metric request is a whole-day scan of the backing layer server-side --
 * the day predicate cannot become an index condition, because the expression index that would
 * make it one cannot be created (text->date is a CoerceViaIO conversion Postgres treats as
 * STABLE, so CREATE INDEX rejects it with 42P17). At radius 7 a single settled scrub issued 15
 * such scans; at radius 1 it issues 3, which covers the next step in either direction -- the
 * only neighbours a keyboard scrub actually reaches before the debounce fires again.
 */
const PREFETCH_RADIUS_DAYS = 1;
const METRIC_STALE_TIME_MS = 5 * 60_000;
/** Short enough that days scrubbed past fall out instead of accumulating. */
const METRIC_GC_TIME_MS = 10 * 60_000;

/** Resolves one metric-at-date collection. Overridable so tests can supply fixtures. */
export type MetricAtDateFetcher = (input: MetricAtDateInput) => Promise<MetricAtDateCollection>;

/** Cache identity of a metric-at-date request. */
export function metricAtDateQueryKey(input: MetricAtDateInput): readonly unknown[] {
  return ["metric-at-date", input.metric, input.date, input.variant, input.bbox ?? null];
}

/** A collection that carries its own reason for being empty. */
function emptyCollection(
  availability: MetricAtDateAvailability,
  reason: string | null
): MetricAtDateCollection {
  return { type: "FeatureCollection", features: [], availability, reason };
}

/**
 * Default transport: the warehouse, in every environment.
 *
 * There is deliberately NO development stand-in here. The one this replaced returned six
 * plausible points stamped with the REQUESTED date and `availability: "published"` --
 * byte-identical in shape to a real observation, and indistinguishable from one on the map.
 * Scrubbing to a day the warehouse holds nothing for painted it as fully observed. Gating
 * that on NODE_ENV was not a defence: `next dev`, `vitest` and every preview deploy all run
 * outside production, which is exactly where the map is looked at while it is being built.
 * A day with no data must come back empty with a reason. See scripts/check-fabricated-
 * observations.mjs, which fails the build if a client module can synthesise one again.
 */
export const fetchMetricAtDate: MetricAtDateFetcher = (input) =>
  getVanillaTrpcClient().environmental.getMetricAtDate.query(input);

export interface UseMetricAtDateOptions {
  /** geo.layers.name of the layer being drawn. */
  layerName: string;
  /** Metric key the server maps to a backing layer and payload field. */
  metric: string;
  /** "west,south,east,north"; omit for an unbounded query. */
  bbox?: string;
  /** False when the layer toggle is off. Never set from "this date has no data". */
  enabled?: boolean;
  /** Override the transport, for tests. */
  fetchMetricAtDate?: MetricAtDateFetcher;
}

export interface UseMetricAtDateResult {
  collection: MetricAtDateCollection;
  availability: MetricAtDateAvailability;
  reason: string | null;
  isLoading: boolean;
  /** True while the pointer is still moving and the query has not caught up. */
  isScrubbing: boolean;
  /** The date the returned collection describes; lags selectedDate while scrubbing. */
  resolvedDate: string;
  variant: MetricVariant;
}

/**
 * Reads one layer's metric for the slider's selected date. Decides availability before
 * querying, so unavailable dates never issue a request.
 */
export function useMetricAtDate(options: UseMetricAtDateOptions): UseMetricAtDateResult {
  const { layerName, metric, bbox, enabled = true } = options;
  const fetcher = options.fetchMetricAtDate ?? fetchMetricAtDate;
  const queryClient = useQueryClient();

  const selectedDate = useTimeSliderStore((state) => state.selectedDate);
  const forecastVariant = useTimeSliderStore((state) => state.forecastVariant);
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  // The store moves with the pointer; only the query waits for the scrub to settle.
  const debouncedDate = useDebounce(selectedDate, SCRUB_SETTLE_MS);

  const layer = findLayerCapability(capabilities, layerName);
  const availabilityBeforeQuery: MetricAtDateAvailability =
    capabilities === null || layer === null
      ? "not_published"
      : layerAvailabilityAt(layer, debouncedDate, forecastVariant, capabilities);
  const variant: MetricVariant =
    capabilities === null ? "observed" : resolveVariant(debouncedDate, capabilities, forecastVariant);

  const shouldQuery = enabled && availabilityBeforeQuery === "published";

  const queryInput = useMemo<MetricAtDateInput>(
    () => ({ metric, date: debouncedDate, variant, bbox }),
    [metric, debouncedDate, variant, bbox]
  );

  const query = useQuery({
    queryKey: metricAtDateQueryKey(queryInput),
    queryFn: () => fetcher(queryInput),
    enabled: shouldQuery,
    staleTime: METRIC_STALE_TIME_MS,
    gcTime: METRIC_GC_TIME_MS,
  });

  // Warm only the neighbourhood a user can reach in a few steps; a full history of
  // ~400 observed days plus the forecast horizon would be hundreds of cache keys.
  useEffect(() => {
    if (!shouldQuery || capabilities === null || layer === null) return;
    for (let offset = -PREFETCH_RADIUS_DAYS; offset <= PREFETCH_RADIUS_DAYS; offset += 1) {
      if (offset === 0) continue;
      const neighbourDate = addDays(debouncedDate, offset);
      if (layerAvailabilityAt(layer, neighbourDate, forecastVariant, capabilities) !== "published") {
        continue;
      }
      const neighbourInput: MetricAtDateInput = {
        metric,
        date: neighbourDate,
        variant: resolveVariant(neighbourDate, capabilities, forecastVariant),
        bbox,
      };
      void queryClient.prefetchQuery({
        queryKey: metricAtDateQueryKey(neighbourInput),
        queryFn: () => fetcher(neighbourInput),
        staleTime: METRIC_STALE_TIME_MS,
        gcTime: METRIC_GC_TIME_MS,
      });
    }
  }, [
    shouldQuery,
    capabilities,
    layer,
    debouncedDate,
    forecastVariant,
    metric,
    bbox,
    fetcher,
    queryClient,
  ]);

  const collection = useMemo<MetricAtDateCollection>(() => {
    if (!shouldQuery) {
      return emptyCollection(
        availabilityBeforeQuery,
        describeAvailability(availabilityBeforeQuery, layerName)
      );
    }
    if (query.data !== undefined) return query.data;
    if (query.isError) {
      // Not `not_published`: that is a claim about the warehouse, and a failed request
      // knows nothing about the warehouse. See MetricAtDateAvailability."request_failed".
      return emptyCollection("request_failed", `Could not load ${layerName} for ${debouncedDate}.`);
    }
    // In flight: empty, but not yet a claim that nothing is published.
    return emptyCollection("published", null);
  }, [shouldQuery, availabilityBeforeQuery, layerName, query.data, query.isError, debouncedDate]);

  return {
    collection,
    availability: collection.availability,
    reason: collection.reason,
    isLoading: shouldQuery && query.isPending,
    isScrubbing: selectedDate !== debouncedDate,
    resolvedDate: debouncedDate,
    variant,
  };
}
