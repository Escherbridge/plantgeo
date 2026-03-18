import { useState, useEffect } from "react";
import { useDebounce } from "./useDebounce";
import type { NormalizedGeocodingResult } from "@/lib/server/services/geocoding";

interface UseGeocodeOptions {
  lat?: number;
  lon?: number;
}

export function useGeocode(query: string, options?: UseGeocodeOptions) {
  const [results, setResults] = useState<NormalizedGeocodingResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    if (debouncedQuery.length < 2) {
      setResults([]);
      return;
    }

    const controller = new AbortController();

    const fetchResults = async () => {
      setIsLoading(true);
      setError(null);

      const params = new URLSearchParams({
        q: debouncedQuery,
        limit: "5",
      });
      if (options?.lat !== undefined) params.set("lat", String(options.lat));
      if (options?.lon !== undefined) params.set("lon", String(options.lon));

      try {
        const res = await fetch(`/api/geocode?${params}`, {
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`Geocode request failed: ${res.status}`);
        const data = (await res.json()) as { results: NormalizedGeocodingResult[] };
        setResults(data.results);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setIsLoading(false);
      }
    };

    fetchResults();
    return () => controller.abort();
  }, [debouncedQuery, options?.lat, options?.lon]);

  return { results, isLoading, error };
}
