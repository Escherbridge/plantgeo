"use client";

import { useState, useEffect, useCallback, useRef } from "react";
const EMPTY_FIRE_DATA: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

interface UseFireDataReturn {
  /** Published fire detections accepted into the platform database. */
  data: GeoJSON.FeatureCollection;
  /** Total count of published fire detections. */
  count: number;
  /** Whether initial fetch is in progress. */
  isLoading: boolean;
  /** Error message if fetch failed. */
  error: string | null;
  /** Manually refetch fire data. */
  refetch: () => void;
}

const REFETCH_INTERVAL_MS = 120_000; // 2 minutes

/**
 * Published fire detections for one day, or for the live FIRMS lookback window.
 *
 * @param enabled the layer's switch position; nothing is fetched while it is off.
 * @param date the time slider's settled day, or undefined when the selection IS the server's
 *   today. Undefined is not a default to tidy away: it is what asks for the live window, which
 *   is the exact request first paint has always made and the only one whose cost is measured.
 *   Sending today's date explicitly instead would mint a second, colder cache entry for the
 *   same answer.
 */
export function useFireData(enabled = true, date?: string): UseFireDataReturn {
  const [data, setData] = useState<GeoJSON.FeatureCollection>(EMPTY_FIRE_DATA);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Monotonic id of the newest request issued. A response may only write state when it is
  // still the newest -- see the ordering note on fetchFires.
  const latestRequestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const etagCacheRef = useRef<Map<string, string>>(new Map());

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
      const cachedEtag = etagCacheRef.current.get(cacheKey);
      if (cachedEtag) {
        headers["If-None-Match"] = cachedEtag;
      }

      const res = await fetch(
        date === undefined ? "/api/fires" : `/api/fires?date=${encodeURIComponent(date)}`,
        { signal: controller.signal, headers }
      );

      if (res.status === 304) {
        // Data not modified - retain existing data, clear loading flag
        if (isStale()) return;
        return;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const newEtag = res.headers.get("ETag");
      if (newEtag) {
        etagCacheRef.current.set(cacheKey, newEtag);
      }

      const geojson: GeoJSON.FeatureCollection = await res.json();

      if (isStale()) return;
      setData(geojson.features ? geojson : EMPTY_FIRE_DATA);
    } catch (err) {
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
    refetch: fetchFires,
  };
}
