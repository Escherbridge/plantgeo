import { getRedis } from "@/lib/server/redis";

const PLANTCOMMERCE_API_URL = process.env.PLANTCOMMERCE_API_URL;

/** TTL 1 hour — supplier availability changes slowly */
const PLANTCOMMERCE_CACHE_TTL = 60 * 60;

/**
 * Simple geohash-like cache key prefix.
 * Encodes lat/lon to precision-5 equivalent by truncating to 2 decimal places (~1km).
 * Avoids the ngeohash dependency.
 */
export function encodeGeohashKey(lat: number, lon: number): string {
  return `${lat.toFixed(2)}:${lon.toFixed(2)}`;
}

/**
 * Retry helper with exponential backoff.
 * Retries only on network errors or 5xx responses.
 * @param fn async function that returns a Response
 * @param maxRetries number of additional attempts after the first (default 2)
 */
export async function retryWithBackoff(
  fn: () => Promise<Response>,
  maxRetries = 2
): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fn();
      // Do not retry on 4xx client errors
      if (response.status < 500) {
        return response;
      }
      // 5xx: retry after backoff (except on last attempt)
      if (attempt < maxRetries) {
        await new Promise((resolve) =>
          setTimeout(resolve, Math.min(200 * Math.pow(2, attempt), 2000))
        );
        continue;
      }
      return response;
    } catch (err) {
      lastError = err;
      if (attempt < maxRetries) {
        await new Promise((resolve) =>
          setTimeout(resolve, Math.min(200 * Math.pow(2, attempt), 2000))
        );
      }
    }
  }
  throw lastError;
}

export interface Supplier {
  id: string;
  name: string;
  strategyTypes: string[];
  region: string;
  rating: number;
  productsAvailable: string[];
  url?: string;
}

interface PlantCommerceResponse {
  suppliers: Supplier[];
}

/**
 * Fetch PlantCommerce suppliers for a given strategy type and location.
 * Returns empty array gracefully when PLANTCOMMERCE_API_URL is not configured
 * or when the API is unreachable.
 *
 * Caches responses in Redis for 1 hour.
 * Retries once on 5xx errors.
 */
export async function getStrategySuppliers(
  strategyId: string,
  lat: number,
  lon: number
): Promise<Supplier[]> {
  if (!PLANTCOMMERCE_API_URL) {
    // Graceful fallback: PlantCommerce integration not configured
    return [];
  }

  const cacheKey = `suppliers:${strategyId}:${encodeGeohashKey(lat, lon)}`;
  const r = getRedis();

  try {
    const cached = await r.get(cacheKey);
    if (cached) {
      return JSON.parse(cached) as Supplier[];
    }
  } catch {
    // Redis unavailable — proceed without cache
  }

  const url = new URL(`${PLANTCOMMERCE_API_URL}/api/v1/strategy-suppliers`);
  url.searchParams.set("strategy", strategyId);
  url.searchParams.set("lat", String(lat));
  url.searchParams.set("lon", String(lon));

  const fetchWithTimeout = (): Promise<Response> => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    return fetch(url.toString(), {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    }).finally(() => clearTimeout(timeoutId));
  };

  let response: Response;
  try {
    response = await retryWithBackoff(fetchWithTimeout, 2);
  } catch {
    // Network error, timeout, or abort — return empty gracefully
    return [];
  }

  if (!response.ok) {
    return [];
  }

  let suppliers: Supplier[] = [];
  try {
    const data = (await response.json()) as PlantCommerceResponse;
    suppliers = data.suppliers ?? [];
  } catch {
    return [];
  }

  try {
    const r2 = getRedis();
    await r2.setex(cacheKey, PLANTCOMMERCE_CACHE_TTL, JSON.stringify(suppliers));
  } catch {
    // Cache write failure is non-fatal
  }

  return suppliers;
}
