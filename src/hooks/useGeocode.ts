import { useState, useEffect, useRef } from "react";
import { useDebounce } from "./useDebounce";
import { readGeocodingResults, type NormalizedGeocodingResult } from "@/lib/geocoding";

interface UseGeocodeOptions {
  lat?: number;
  lon?: number;
}

export function useGeocode(query: string, options?: UseGeocodeOptions) {
  const [results, setResults] = useState<NormalizedGeocodingResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    if (debouncedQuery.length < 2) {
      setResults([]);
      setError(null);
      setIsLoading(false);
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
        const nextResults = await readGeocodingResults(res);
        if (controller.signal.aborted || requestId !== requestIdRef.current) return;
        setResults(nextResults);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        if (controller.signal.aborted || requestId !== requestIdRef.current) return;
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        if (requestId === requestIdRef.current) setIsLoading(false);
      }
    };

    fetchResults();
    return () => controller.abort();
  }, [debouncedQuery, options?.lat, options?.lon]);

  return { results, isLoading, error };
}
