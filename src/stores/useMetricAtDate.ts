"use client";

import { useEffect, useMemo, useRef } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDebounce } from "@/hooks/useDebounce";
import { getVanillaTrpcClient } from "@/lib/trpc/client";
import type { LayerToggleId } from "@/lib/map/layer-registry";
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
  isWithinCoverageGap,
  layerAvailabilityAt,
  resolveLayerDate,
  resolveVariant,
  useTimeSliderStore,
  warehouseLayerNameFor,
} from "./time-slider-store";

/**
 * Milliseconds of stillness before a scrub turns into a request.
 *
 * Exported and shared rather than restated per caller: a row's slider writes a new day on every
 * pointer tick, and every consumer that turns that day into a request must settle on the SAME
 * boundary. Two different settle windows would issue two waves of requests per scrub for the
 * same day -- see `useDebouncedLayerDay` in `src/lib/map/layer-toggle-context.ts`.
 */
export const SCRUB_SETTLE_MS = 250;
/**
 * Days either side of THIS layer's day that are worth warming.
 *
 * ONE, not seven. Every metric request is a whole-day scan of the backing layer server-side --
 * the day predicate cannot become an index condition, because the expression index that would
 * make it one cannot be created (text->date is a CoerceViaIO conversion Postgres treats as
 * STABLE, so CREATE INDEX rejects it with 42P17). At radius 7 a single settled scrub issued 15
 * such scans; at radius 1 it issues 3, which covers the next step in either direction -- the
 * only neighbours a keyboard scrub actually reaches before the debounce fires again.
 *
 * Held at 1 under per-layer dates, because the fan-out got SMALLER, not larger. With one global
 * day every mounted instance of this hook re-ran its prefetch on every settled scrub, so a
 * single scrub cost 3 scans per mounted layer; now a scrub moves one layer's day, the other
 * layers' `debouncedDate` values do not change, their effects do not re-run, and the scrub costs
 * 3 scans total. The one place the fan-out is unchanged is first paint, where each layer settles
 * its own default day once -- and the two skips below cut that, because a layer defaulting to
 * its own `latestObservedDate` would otherwise always warm the day after it, which is by
 * definition unpublished.
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
  /**
   * The layer ROW this read belongs to. Supplies BOTH the day and the warehouse layer.
   *
   * A toggle id rather than a `geo.layers.name`, and rather than both: days are keyed by
   * LayerToggleId because the slider lives on a row, capabilities are keyed by warehouse name,
   * and `LAYER_REGISTRY[…].warehouseLayerName` is the one bridge between them. Taking both from
   * the caller would let a row read one layer's day against another layer's capability, and
   * nothing downstream could detect the mismatch.
   */
  layerId: LayerToggleId;
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
  /**
   * The date the returned collection describes. Lags this layer's day while scrubbing, and also
   * lags `resolvedDate`'s own target while the previous day's collection is retained during a
   * load -- it always names the day the features in hand belong to, never the day requested.
   *
   * Load-bearing under per-layer dates, not less so: with each layer on its own day, a caption
   * built from the row's slider position instead of this value would mislabel a retained frame
   * AND put it beside other rows showing genuinely different days, which is unrecoverable for a
   * reader.
   *
   * Names `debouncedDate` -- never the stale `settledDateRef` -- whenever `shouldQuery` is
   * false. A disabled query still carries `isPlaceholderData: true` from `keepPreviousData`
   * (TanStack keeps surfacing the last collection's `data` as a placeholder even once the query
   * that produced it stops running), but `collection` in that branch is the CURRENT day's
   * refusal, built from `availabilityBeforeQuery` for `debouncedDate` -- not a retained frame at
   * all. Reading the placeholder flag alone would therefore caption a fresh refusal with the
   * previous settled day.
   */
  resolvedDate: string;
  /**
   * True while the collection is the PREVIOUS day's, retained so the layer does not blank
   * during a load. Consumers that assert a date to the user must read `resolvedDate` rather
   * than the slider's day, and may dim; consumers that only draw geometry can ignore it.
   */
  isShowingPreviousDay: boolean;
  variant: MetricVariant;
}

/**
 * Reads one layer's metric for THAT layer's own selected date. Decides availability before
 * querying, so unavailable dates never issue a request.
 */
export function useMetricAtDate(options: UseMetricAtDateOptions): UseMetricAtDateResult {
  const { layerId, metric, bbox, enabled = true } = options;
  const fetcher = options.fetchMetricAtDate ?? fetchMetricAtDate;
  const queryClient = useQueryClient();

  // Selects the resolved DAY, not the `layerDates` record: the record's identity changes on
  // every write to any layer, so selecting it would re-render -- and re-debounce -- every
  // layer's reader on every pointer tick of any one layer's scrub.
  const selectedDate = useTimeSliderStore((state) =>
    resolveLayerDate(state.layerDates, state.capabilities, layerId)
  );
  const forecastVariant = useTimeSliderStore((state) => state.forecastVariant);
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  // The store moves with the pointer; only the query waits for the scrub to settle.
  const debouncedDate = useDebounce(selectedDate, SCRUB_SETTLE_MS);

  // The `geo.layers` name behind the row, and the only place this hook crosses between the two
  // vocabularies. Null for a toggle no warehouse layer backs (drought lives in
  // geo.drought_areas), which resolves to a null capability and so to `not_published` below --
  // the same answer as a layer the server has not published a capability row for.
  const warehouseLayerName = warehouseLayerNameFor(layerId);
  // A user-facing name, never the toggle id when a real one exists; mirrors `useLayerRenderState`.
  const layerName = warehouseLayerName ?? layerId;
  const layer =
    warehouseLayerName === null ? null : findLayerCapability(capabilities, warehouseLayerName);
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
    // Hold the last day's collection while the next one loads instead of dropping to
    // `undefined`. Without it every date change unmounts the layer's features for the length
    // of a warehouse round trip, so a scrub reads as the map repeatedly going blank and
    // refilling rather than as one thing changing.
    //
    // Safe ONLY because `resolvedDate` below follows the data rather than the request: a
    // retained collection is still labelled with the day it actually describes, so no consumer
    // can render yesterday's features under today's date. That is the same discipline
    // `MetricAtDateAvailability."request_failed"` exists for -- the hook may lag, it may not
    // misstate.
    placeholderData: keepPreviousData,
  });

  // The day the collection in hand actually describes. `debouncedDate` is the day being asked
  // for, and while a placeholder is showing those are different.
  const settledDateRef = useRef(debouncedDate);
  if (query.data !== undefined && !query.isPlaceholderData) {
    settledDateRef.current = debouncedDate;
  }
  // `shouldQuery` gates the placeholder read, not just `query.isPlaceholderData` alone: TanStack
  // keeps `isPlaceholderData` true off `keepPreviousData` even after the query is DISABLED (no
  // fetch is running, but the last successful data still stands in), and `shouldQuery === false`
  // is exactly the branch below that discards that placeholder and returns a fresh refusal for
  // `debouncedDate` instead. Reading `settledDateRef.current` there would name the wrong day for
  // the collection this hook actually returns -- the one bug `resolvedDate` exists not to have.
  const describedDate =
    shouldQuery && query.isPlaceholderData ? settledDateRef.current : debouncedDate;

  // Warm only the neighbourhood a user can reach in a few steps from THIS layer's own day; a
  // full history of ~400 observed days plus the forecast horizon would be hundreds of cache keys.
  useEffect(() => {
    if (!shouldQuery || capabilities === null || layer === null) return;
    for (let offset = -PREFETCH_RADIUS_DAYS; offset <= PREFETCH_RADIUS_DAYS; offset += 1) {
      if (offset === 0) continue;
      const neighbourDate = addDays(debouncedDate, offset);
      if (layerAvailabilityAt(layer, neighbourDate, forecastVariant, capabilities) !== "published") {
        continue;
      }
      // Past this layer's newest published day. `layerAvailabilityAt` still calls such a day
      // "published" -- it is inside the axis and not in the future -- so without this the
      // default case warms a guaranteed-empty day for EVERY layer on first paint, since every
      // layer now opens on exactly its `latestObservedDate`.
      if (layer.latestObservedDate !== null && neighbourDate > layer.latestObservedDate) continue;
      // Inside a hole the server already told us about. Skipping is not an availability claim:
      // the day stays scrubbable and still issues a real request if the user lands on it. This
      // only declines to spend a whole-day scan warming one.
      if (isWithinCoverageGap(layer, neighbourDate)) continue;
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
    // `isPending` is false while a placeholder stands in, so a retained frame would otherwise
    // report as settled. Fetching is the honest signal now that the collection can outlive the
    // request that produced it.
    isLoading: shouldQuery && (query.isPending || query.isPlaceholderData),
    isScrubbing: selectedDate !== debouncedDate,
    resolvedDate: describedDate,
    isShowingPreviousDay: shouldQuery && query.isPlaceholderData,
    variant,
  };
}
