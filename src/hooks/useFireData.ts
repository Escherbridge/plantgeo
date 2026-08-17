"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { createBudgetedFetch } from "@/lib/net/request-budget";

const EMPTY_FIRE_DATA: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/** Module-level so every `useFireData` instance shares one lane in the request budget -- see
 *  src/lib/net/AGENTS.md "Both transports". */
const budgetedFetch = createBudgetedFetch("fires");

/** One request's cached answer: the ETag it was served under, and the payload that ETag names. */
export interface CachedFireResponse {
  etag: string;
  data: GeoJSON.FeatureCollection;
}

/** Bounded LRU cache size for `responseCacheRef` -- see src/hooks/AGENTS.md §useFireData for the
 *  per-entry footprint this is sized against (worst case single-digit MB, not hundreds). */
export const MAX_CACHED_RESPONSES = 4;

/** Upserts one cached response, refreshing its recency, evicting the oldest past the bound. */
export function rememberFireResponse(
  cache: Map<string, CachedFireResponse>,
  cacheKey: string,
  entry: CachedFireResponse
): void {
  cache.delete(cacheKey); // drop and reinsert so this key reads newest for eviction order
  cache.set(cacheKey, entry);
  if (cache.size > MAX_CACHED_RESPONSES) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey !== undefined) cache.delete(oldestKey);
  }
}

interface UseFireDataReturn {
  /** Published fire detections. May be retained from a previous request -- see
   *  `isStaleForRequestedDate` and src/hooks/AGENTS.md §useFireData before captioning this as
   *  the requested day's own answer. */
  data: GeoJSON.FeatureCollection;
  /** Total count of published fire detections. */
  count: number;
  /** Whether a fetch is currently in flight for the requested day. */
  isLoading: boolean;
  /** Error message if fetch failed. */
  error: string | null;
  /** True whenever `data` was NOT fetched for the currently requested `date`. False only once
   *  `data` demonstrably answers for `date` itself -- see src/hooks/AGENTS.md §useFireData. */
  isStaleForRequestedDate: boolean;
  /** The day `data` actually answers for: undefined for the live window, or before anything has
   *  ever loaded. Compares directly against a caller's own `date`. */
  dataDate: string | undefined;
  /** Manually refetch fire data. */
  refetch: () => void;
}

const REFETCH_INTERVAL_MS = 120_000; // 2 minutes

/**
 * Published fire detections for one day, or for the live FIRMS lookback window.
 *
 * @param enabled the layer's switch position; nothing is fetched while it is off.
 * @param date the time slider's settled day, or undefined when the selection IS the server's
 *   today -- see src/hooks/AGENTS.md §useFireData for what "omitted" means for THIS route
 *   specifically, and why sending today's date explicitly would mint a colder cache entry for
 *   the same answer.
 */
export function useFireData(enabled = true, date?: string): UseFireDataReturn {
  const [data, setData] = useState<GeoJSON.FeatureCollection>(EMPTY_FIRE_DATA);
  // Which day `data` actually answers for; see src/hooks/AGENTS.md §useFireData for why this is
  // a separate flag from `hasLoadedOnce` rather than inferred from `dataDate` alone.
  const [dataDate, setDataDate] = useState<string | undefined>(undefined);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Monotonic id of the newest request issued. A response may only write state when it is
  // still the newest -- see the ordering note on fetchFires.
  const latestRequestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // Payload cached alongside its ETag, keyed by cacheKey (`date ?? "live"`) -- see
  // src/hooks/AGENTS.md §useFireData for why a payload-less ETag cache is unsound here, and for
  // the bound's memory footprint.
  const responseCacheRef = useRef<Map<string, CachedFireResponse>>(new Map());

  const fetchFires = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = latestRequestRef.current + 1;
    latestRequestRef.current = requestId;

    const isStale = () => requestId !== latestRequestRef.current;
    const cacheKey = date ?? "live";

    try {
      setIsLoading(true);
      setError(null);

      const headers: Record<string, string> = {};
      const cached = responseCacheRef.current.get(cacheKey);
      if (cached) {
        headers["If-None-Match"] = cached.etag;
      }

      const res = await budgetedFetch(
        date === undefined ? "/api/fires" : `/api/fires?date=${encodeURIComponent(date)}`,
        { signal: controller.signal, headers }
      );

      if (res.status === 304) {
        // Paint the cached payload this ETag names -- see src/hooks/AGENTS.md §useFireData for
        // why leaving `data` untouched here is unsafe.
        if (isStale() || !cached) return;
        rememberFireResponse(responseCacheRef.current, cacheKey, cached); // refresh recency
        setData(cached.data);
        setDataDate(date);
        setHasLoadedOnce(true);
        return;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const geojson: GeoJSON.FeatureCollection = await res.json();
      const collection = geojson.features ? geojson : EMPTY_FIRE_DATA;

      const newEtag = res.headers.get("ETag");
      if (newEtag) {
        rememberFireResponse(responseCacheRef.current, cacheKey, {
          etag: newEtag,
          data: collection,
        });
      }

      if (isStale()) return;
      setData(collection);
      setDataDate(date);
      setHasLoadedOnce(true);
    } catch (err) {
      // Deliberately does NOT touch `data`/`dataDate` -- retention is the point; see
      // src/hooks/AGENTS.md §useFireData.
      if (controller.signal.aborted || isStale()) return;
      setError(err instanceof Error ? err.message : "Failed to fetch fire data");
    } finally {
      if (!isStale()) setIsLoading(false);
    }
  }, [date]);

  useEffect(() => {
    if (!enabled) return;

    const initialFetchTimer = setTimeout(() => void fetchFires(), 0);

    // Polled only on the live window. A past day's detections are settled -- FIRMS does not
    // revise them -- so re-requesting one every two minutes would be pure load for a byte-
    // identical answer, and the effect already refetches when the day itself changes.
    if (date === undefined) {
      intervalRef.current = setInterval(fetchFires, REFETCH_INTERVAL_MS);
    }

    return () => {
      clearTimeout(initialFetchTimer);
      if (intervalRef.current) clearInterval(intervalRef.current);
      // Retiring this day's request as the effect tears down: without it, a fetch issued for
      // the day being left can still resolve and win.
      abortRef.current?.abort();
    };
  }, [enabled, date, fetchFires]);

  return {
    data,
    count: data.features.length,
    isLoading,
    error,
    isStaleForRequestedDate: !hasLoadedOnce || dataDate !== date,
    dataDate,
    refetch: fetchFires,
  };
}
