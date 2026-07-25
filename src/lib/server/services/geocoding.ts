import { z } from "zod";
import type {
  GeocodingResultType,
  NormalizedGeocodingResult,
} from "@/lib/geocoding";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";

const MAX_GEOCODING_RESPONSE_BYTES = 512 * 1024;
const GEOCODING_TIMEOUT_MS = 5_000;

const PhotonPropertiesSchema = z
  .object({
    name: z.string().max(500).optional(),
    street: z.string().max(500).optional(),
    housenumber: z.string().max(100).optional(),
    postcode: z.string().max(100).optional(),
    city: z.string().max(500).optional(),
    state: z.string().max(500).optional(),
    country: z.string().max(500).optional(),
    osm_key: z.string().max(100).optional(),
    osm_value: z.string().max(100).optional(),
  })
  .strip();

const PhotonFeatureSchema = z
  .object({
    type: z.literal("Feature"),
    geometry: z
      .object({
        type: z.literal("Point"),
        coordinates: z.tuple([
          z.number().finite().min(-180).max(180),
          z.number().finite().min(-90).max(90),
        ]),
      })
      .strict(),
    properties: PhotonPropertiesSchema,
  })
  .strip();

const PhotonResponseSchema = z
  .object({
    type: z.literal("FeatureCollection"),
    features: z.array(PhotonFeatureSchema).max(20),
  })
  .strip();

const OptionalCoordinateParameters = {
  lat: z.coerce.number().finite().min(-90).max(90).optional(),
  lon: z.coerce.number().finite().min(-180).max(180).optional(),
};

export const ForwardGeocodeQuerySchema = z
  .object({
    q: z.string().trim().min(2).max(200),
    limit: z.coerce.number().int().min(1).max(10).default(5),
    lang: z.string().trim().regex(/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/).optional(),
    ...OptionalCoordinateParameters,
  })
  .strict()
  .superRefine((value, context) => {
    if ((value.lat === undefined) !== (value.lon === undefined)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "lat and lon must be provided together",
      });
    }
  });

export const ReverseGeocodeQuerySchema = z
  .object({
    lat: z.coerce.number().finite().min(-90).max(90),
    lon: z.coerce.number().finite().min(-180).max(180),
    limit: z.coerce.number().int().min(1).max(5).default(1),
    radius: z.coerce.number().finite().positive().max(50).optional(),
    lang: z.string().trim().regex(/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/).optional(),
  })
  .strict();

export type GeocodingResult = z.infer<typeof PhotonResponseSchema>;
export type ResultType = GeocodingResultType;
export type { NormalizedGeocodingResult } from "@/lib/geocoding";

function endpoint(path: "/api" | "/reverse"): URL {
  const url = providerUrl("PHOTON_URL", "http://localhost:2322");
  url.pathname = `${url.pathname.replace(/\/$/, "")}${path}`;
  return url;
}

function normalizeResultType(properties: z.infer<typeof PhotonPropertiesSchema>): ResultType {
  const { osm_key, osm_value } = properties;

  if (osm_value === "house" || osm_value === "apartments" || osm_value === "residential") {
    return "house";
  }
  if (osm_key === "highway") return "street";
  if (
    osm_value === "city" ||
    osm_value === "town" ||
    osm_value === "village" ||
    osm_value === "hamlet" ||
    osm_value === "municipality"
  ) {
    return "city";
  }
  if (osm_key === "place" && osm_value === "state") return "state";
  if (osm_key === "place" && osm_value === "country") return "country";
  return "other";
}

function formatDisplayName(properties: z.infer<typeof PhotonPropertiesSchema>): string {
  const parts: string[] = [];
  if (properties.name) parts.push(properties.name);

  const streetPart = [properties.street, properties.housenumber].filter(Boolean).join(" ");
  if (streetPart && streetPart !== properties.name) parts.push(streetPart);
  if (properties.city && properties.city !== properties.name) parts.push(properties.city);
  if (properties.state && properties.state !== properties.name) parts.push(properties.state);
  if (properties.country && properties.country !== properties.name) parts.push(properties.country);
  return parts.join(", ") || "Unknown location";
}

export function normalizeResults(result: GeocodingResult): NormalizedGeocodingResult[] {
  return result.features.map((feature, index) => ({
    id: `result-${index}-${feature.geometry.coordinates.join(",")}`,
    type: normalizeResultType(feature.properties),
    name: feature.properties.name || feature.properties.street || "Unknown",
    displayName: formatDisplayName(feature.properties),
    coordinates: feature.geometry.coordinates,
    properties: feature.properties,
  }));
}

async function requestPhoton(url: URL): Promise<GeocodingResult> {
  const data = await fetchBoundedJson(url, { method: "GET" }, {
    maxBytes: MAX_GEOCODING_RESPONSE_BYTES,
    timeoutMs: GEOCODING_TIMEOUT_MS,
  });
  const parsed = PhotonResponseSchema.safeParse(data);
  if (!parsed.success) throw new UpstreamPayloadError("Photon returned an invalid response");
  return parsed.data;
}

export async function forwardGeocode(
  query: string,
  options?: {
    limit?: number;
    lang?: string;
    lat?: number;
    lon?: number;
  }
): Promise<GeocodingResult> {
  const validated = ForwardGeocodeQuerySchema.parse({ q: query, ...options });
  const url = endpoint("/api");
  url.searchParams.set("q", validated.q);
  url.searchParams.set("limit", String(validated.limit));
  if (validated.lang) url.searchParams.set("lang", validated.lang);
  if (validated.lat !== undefined && validated.lon !== undefined) {
    url.searchParams.set("lat", String(validated.lat));
    url.searchParams.set("lon", String(validated.lon));
  }
  return requestPhoton(url);
}

export async function reverseGeocode(
  lat: number,
  lon: number,
  options?: { radius?: number; limit?: number; lang?: string }
): Promise<GeocodingResult> {
  const validated = ReverseGeocodeQuerySchema.parse({ lat, lon, ...options });
  const url = endpoint("/reverse");
  url.searchParams.set("lat", String(validated.lat));
  url.searchParams.set("lon", String(validated.lon));
  url.searchParams.set("limit", String(validated.limit));
  if (validated.radius !== undefined) url.searchParams.set("radius", String(validated.radius));
  if (validated.lang) url.searchParams.set("lang", validated.lang);
  return requestPhoton(url);
}
