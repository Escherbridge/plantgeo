"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { DEMO_FIRE_POINTS } from "@/lib/map/demo-data";

/** Build demo fallback GeoJSON from DEMO_FIRE_POINTS. */
function buildDemoGeoJSON(): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: DEMO_FIRE_POINTS.map((pt) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [pt.lon, pt.lat] },
      properties: {
        IncidentName: `Demo Fire (${pt.satellite})`,
        IncidentSize: null,
        POOState: "US-WA",
        FireDiscoveryDateTime: new Date(pt.detectedAt).getTime(),
        PercentContained: null,
        brightness: pt.brightness,
        confidence: pt.confidence,
        satellite: pt.satellite,
        _isDemo: true,
      },
    })),
  };
}

interface UseFireDataReturn {
  /** GeoJSON FeatureCollection of active fires (real or demo). */
  data: GeoJSON.FeatureCollection;
  /** Total count of fire incidents. */
  count: number;
  /** Whether initial fetch is in progress. */
  isLoading: boolean;
  /** Whether we're showing demo fallback data. */
  isDemo: boolean;
  /** Error message if fetch failed. */
  error: string | null;
  /** Manually refetch fire data. */
  refetch: () => void;
}

const REFETCH_INTERVAL_MS = 120_000; // 2 minutes

export function useFireData(enabled = true): UseFireDataReturn {
  const [data, setData] = useState<GeoJSON.FeatureCollection>(buildDemoGeoJSON());
  const [isLoading, setIsLoading] = useState(false);
  const [isDemo, setIsDemo] = useState(true);
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

      if (geojson.features && geojson.features.length > 0) {
        setData(geojson);
        setIsDemo(false);
      } else {
        // Empty response — keep demo
        setData(buildDemoGeoJSON());
        setIsDemo(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch fire data");
      // Keep existing data (demo or previous real data)
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;

    // Initial fetch
    fetchFires();

    // Periodic refetch
    intervalRef.current = setInterval(fetchFires, REFETCH_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [enabled, fetchFires]);

  return {
    data,
    count: data.features.length,
    isLoading,
    isDemo,
    error,
    refetch: fetchFires,
  };
}
