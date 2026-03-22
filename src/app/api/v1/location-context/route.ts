import { NextRequest, NextResponse } from "next/server";
import { validateApiKey, checkRateLimit } from "@/lib/server/middleware/api-auth";
import { getSoilProperties, SoilProperties } from "@/lib/server/services/soilgrids";
import { getDroughtClassification } from "@/lib/server/services/drought";
import { getNDVITileUrl } from "@/lib/server/services/vegetation";
import { calculateFireRisk } from "@/lib/server/services/fire-risk";
import { getCurrentWeather } from "@/lib/server/services/weather";
import { db } from "@/lib/server/db";
import { priorityZones } from "@/lib/server/db/schema";
import { sql } from "drizzle-orm";
import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";
const LOCATION_CONTEXT_CACHE_TTL = 15 * 60; // 15 minutes

let redis: Redis | null = null;

function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 3,
      retryStrategy(times) {
        return Math.min(times * 50, 2000);
      },
    });
  }
  return redis;
}

/** Simple geohash-like cache key: truncate to 2 decimal places (~1km precision) */
function locationCacheKey(lat: number, lon: number): string {
  return `location-context:${lat.toFixed(2)}:${lon.toFixed(2)}`;
}

/** Find drought classification label at a lat/lon point by checking FeatureCollection properties */
function droughtClassAtPoint(
  fc: GeoJSON.FeatureCollection,
  lat: number,
  lon: number
): string {
  // The USDM FeatureCollection has features with a DM property (0-4 = D0-D4)
  // and bounding polygons. Without a full PIP library we return the first
  // feature whose bbox roughly contains the point, falling back to "None".
  for (const feature of fc.features) {
    if (!feature.properties) continue;
    const dm = feature.properties["DM"] as number | undefined;
    if (dm === undefined) continue;
    // Use bbox if available
    if (feature.bbox) {
      const [minLon, minLat, maxLon, maxLat] = feature.bbox;
      if (lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat) {
        const labels = ["D0 - Abnormally Dry", "D1 - Moderate", "D2 - Severe", "D3 - Extreme", "D4 - Exceptional"];
        return labels[dm] ?? `D${dm}`;
      }
    }
  }
  return "None";
}

export async function GET(request: NextRequest) {
  // Validate API key
  const authResult = await validateApiKey(request);
  if (!authResult.valid) {
    return NextResponse.json(
      { error: authResult.error ?? "Unauthorized" },
      { status: 401 }
    );
  }

  // Rate limit check
  const { limited, retryAfter } = await checkRateLimit(
    authResult.keyId!,
    authResult.rateLimit ?? 100
  );
  if (limited) {
    return NextResponse.json(
      { error: "Rate limit exceeded" },
      {
        status: 429,
        headers: retryAfter ? { "Retry-After": String(retryAfter) } : {},
      }
    );
  }

  // Parse lat/lon
  const params = request.nextUrl.searchParams;
  const latStr = params.get("lat");
  const lonStr = params.get("lon");

  if (!latStr || !lonStr) {
    return NextResponse.json(
      { error: "Missing required query parameters: lat, lon" },
      { status: 400 }
    );
  }

  const lat = Number(latStr);
  const lon = Number(lonStr);

  if (isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return NextResponse.json(
      { error: "Invalid lat/lon values" },
      { status: 400 }
    );
  }

  // Check cache
  const cacheKey = locationCacheKey(lat, lon);
  try {
    const r = getRedis();
    const cached = await r.get(cacheKey);
    if (cached) {
      return new NextResponse(cached, {
        status: 200,
        headers: { "Content-Type": "application/json", "X-Cache": "HIT" },
      });
    }
  } catch {
    // Redis unavailable — proceed without cache
  }

  // Fetch all data in parallel; partial failures are tolerated
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;

  const [weatherResult, soilResult, droughtResult, priorityZonesResult] =
    await Promise.allSettled([
      getCurrentWeather(lat, lon),
      getSoilProperties(lat, lon),
      getDroughtClassification(),
      db
        .select({
          id: priorityZones.id,
          strategyType: priorityZones.strategyType,
          requestCount: priorityZones.requestCount,
          totalVotes: priorityZones.totalVotes,
          centroidLat: priorityZones.centroidLat,
          centroidLon: priorityZones.centroidLon,
        })
        .from(priorityZones)
        .where(
          sql`
            ${priorityZones.centroidLat} BETWEEN ${lat - 0.5} AND ${lat + 0.5}
            AND ${priorityZones.centroidLon} BETWEEN ${lon - 0.5} AND ${lon + 0.5}
          `
        )
        .limit(10),
    ]);

  // Fire risk — requires weather data
  let fireRisk: number | null = null;
  if (weatherResult.status === "fulfilled") {
    const w = weatherResult.value;
    fireRisk = calculateFireRisk({
      vegetationType: "shrubland", // default; NLCD integration can refine this
      slope: 10,
      aspect: 180,
      humidity: w.humidity,
      windSpeed: w.windSpeed,
    });
  }

  // Drought classification at point
  let droughtClass: string | null = null;
  if (droughtResult.status === "fulfilled") {
    droughtClass = droughtClassAtPoint(droughtResult.value, lat, lon);
  }

  // Soil properties
  let soilProperties: SoilProperties | null = null;
  if (soilResult.status === "fulfilled") {
    soilProperties = soilResult.value;
  }

  // NDVI tile URL for current month
  const ndviTileUrl = getNDVITileUrl(year, month);

  // Priority zones near the point
  const nearbyZones =
    priorityZonesResult.status === "fulfilled"
      ? priorityZonesResult.value
      : [];

  // Bounding box: 0.5-degree buffer around point
  const bbox: [number, number, number, number] = [
    lon - 0.5,
    lat - 0.5,
    lon + 0.5,
    lat + 0.5,
  ];

  const response = {
    lat,
    lon,
    fireRisk,
    droughtClass,
    soilProperties,
    ndviTileUrl,
    priorityZones: nearbyZones,
    bbox,
    generatedAt: now.toISOString(),
  };

  const responseBody = JSON.stringify(response);

  // Cache result
  try {
    const r = getRedis();
    await r.setex(cacheKey, LOCATION_CONTEXT_CACHE_TTL, responseBody);
  } catch {
    // Cache write failure is non-fatal
  }

  return new NextResponse(responseBody, {
    status: 200,
    headers: { "Content-Type": "application/json", "X-Cache": "MISS" },
  });
}
