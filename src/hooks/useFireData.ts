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

export function useFireData(enabled = true): UseFireDataReturn {
  const [data, setData] = useState<GeoJSON.FeatureCollection>(EMPTY_FIRE_DATA);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchFires = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const res = await fetch("/api/fires");

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const geojson: GeoJSON.FeatureCollection = await res.json();

      setData(geojson.features ? geojson : EMPTY_FIRE_DATA);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch fire data");
      // Keep the last accepted database response during a transient error.
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;

    const initialFetchTimer = setTimeout(() => void fetchFires(), 0);

    // Periodic refetch
    intervalRef.current = setInterval(fetchFires, REFETCH_INTERVAL_MS);

    return () => {
      clearTimeout(initialFetchTimer);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [enabled, fetchFires]);

  return {
    data,
    count: data.features.length,
    isLoading,
    error,
    refetch: fetchFires,
  };
}
