import { getRedis } from "@/lib/server/redis";

const DROUGHT_CACHE_KEY = "drought:current";
const DROUGHT_CACHE_TTL = 6 * 60 * 60; // 6 hours in seconds

/**
 * Fetch current US Drought Monitor GeoJSON classification.
 * Caches result in Redis for 6 hours.
 */
export async function getDroughtClassification(): Promise<GeoJSON.FeatureCollection> {
  const r = getRedis();

  const cached = await r.get(DROUGHT_CACHE_KEY);
  if (cached) {
    return JSON.parse(cached) as GeoJSON.FeatureCollection;
  }

  const url = "https://droughtmonitor.unl.edu/api/webservice/currentouslookup/";

  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(
      `USDM API error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as GeoJSON.FeatureCollection;

  await r.setex(DROUGHT_CACHE_KEY, DROUGHT_CACHE_TTL, JSON.stringify(data));

  return data;
}

/**
 * Fetch US Drought Monitor GeoJSON for a specific weekly date.
 * @param date ISO date string e.g. "2024-01-02"
 */
export async function getDroughtByDate(
  date: string
): Promise<GeoJSON.FeatureCollection> {
  const cacheKey = `drought:date:${date}`;
  const r = getRedis();

  const cached = await r.get(cacheKey);
  if (cached) {
    return JSON.parse(cached) as GeoJSON.FeatureCollection;
  }

  // USDM historical endpoint — date must be a Tuesday (weekly release)
  const url = `https://droughtmonitor.unl.edu/api/webservice/ouslookup/?startdate=${date}&enddate=${date}`;

  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(
      `USDM historical API error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as GeoJSON.FeatureCollection;

  // Historical data is immutable — cache for 24h
  await r.setex(cacheKey, 86400, JSON.stringify(data));

  return data;
}
