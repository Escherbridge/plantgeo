import Redis from "ioredis";
import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

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

/** TTL 24 hours — USDA SSURGO data is stable */
const USDA_CACHE_TTL = 60 * 60 * 24;

const USDA_WFS_BASE =
  "https://SDMDataAccess.nrcs.usda.gov/Spatial/SDM.wfs";
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 15_000;

export interface SoilSurveyProperties {
  mukey: string;
  muname: string;
  soilSeries: string;
  drainageClass: string;
  hydric: boolean;
  landCapabilityClass: string;
}

interface WFSFeature {
  type: "Feature";
  id?: string;
  geometry: GeoJSON.Geometry;
  properties: Record<string, unknown>;
}

interface WFSResponse {
  type: "FeatureCollection";
  features: WFSFeature[];
}

/**
 * Fetch SSURGO map unit polygons from USDA Soil Data Mart WFS for a bounding box.
 * Returns GeoJSON FeatureCollection with soil survey properties.
 * @param bbox "west,south,east,north"
 */
export async function getSoilSurvey(
  bbox: string
): Promise<GeoJSON.FeatureCollection> {
  const cacheKey = `usda-soil:${bbox}`;
  const r = getRedis();

  const cached = await r.get(cacheKey);
  if (cached) {
    return JSON.parse(cached) as GeoJSON.FeatureCollection;
  }

  const params = new URLSearchParams({
    SERVICE: "WFS",
    REQUEST: "GetFeature",
    TYPENAME: "MapunitPoly",
    BBOX: bbox,
    outputFormat: "application/json",
  });

  const raw = (await fetchBoundedJson(
    `${USDA_WFS_BASE}?${params}`,
    { headers: { Accept: "application/json" } },
    { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS, revalidateSeconds: 86400 }
  )) as WFSResponse;

  // Map raw WFS properties to our typed schema
  const featureCollection: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: (raw.features ?? []).map((f) => {
      const p = f.properties ?? {};
      const props: SoilSurveyProperties = {
        mukey: String(p["mukey"] ?? p["MUKEY"] ?? ""),
        muname: String(p["muname"] ?? p["MUNAME"] ?? "Unknown"),
        soilSeries: String(p["compname"] ?? p["COMPNAME"] ?? "Unknown"),
        drainageClass: normalizeDrainageClass(
          String(p["drainagecl"] ?? p["DRAINAGECL"] ?? "")
        ),
        hydric: Boolean(p["hydricrating"] ?? p["HYDRICRATING"]),
        landCapabilityClass: String(p["nirrcapcl"] ?? p["NIRRCAPCL"] ?? ""),
      };
      return {
        type: "Feature" as const,
        geometry: f.geometry,
        properties: props,
      };
    }),
  };

  await r.setex(cacheKey, USDA_CACHE_TTL, JSON.stringify(featureCollection));

  return featureCollection;
}

function normalizeDrainageClass(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes("well")) return "well-drained";
  if (lower.includes("somewhat poorly")) return "somewhat-poorly-drained";
  if (lower.includes("poorly")) return "poorly-drained";
  if (lower.includes("very poorly")) return "very-poorly-drained";
  if (lower.includes("moderately well")) return "moderately-well-drained";
  if (lower.includes("excessively")) return "excessively-drained";
  return raw || "unknown";
}
