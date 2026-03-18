import { z } from "zod";

const PHOTON_URL = process.env.PHOTON_URL || "http://localhost:2322";

const PhotonPropertiesSchema = z
  .object({
    name: z.string().optional(),
    street: z.string().optional(),
    housenumber: z.string().optional(),
    postcode: z.string().optional(),
    city: z.string().optional(),
    state: z.string().optional(),
    country: z.string().optional(),
    osm_key: z.string().optional(),
    osm_value: z.string().optional(),
  })
  .passthrough();

const PhotonFeatureSchema = z.object({
  type: z.literal("Feature"),
  geometry: z.object({
    type: z.literal("Point"),
    coordinates: z.tuple([z.number(), z.number()]),
  }),
  properties: PhotonPropertiesSchema,
});

const PhotonResponseSchema = z.object({
  type: z.literal("FeatureCollection"),
  features: z.array(PhotonFeatureSchema),
});

export type GeocodingResult = z.infer<typeof PhotonResponseSchema>;
export type ResultType = "house" | "street" | "city" | "state" | "country" | "other";

export interface NormalizedGeocodingResult {
  id: string;
  type: ResultType;
  name: string;
  displayName: string;
  coordinates: [number, number];
  properties: Record<string, string | undefined>;
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
    properties: feature.properties as Record<string, string | undefined>,
  }));
}

export async function forwardGeocode(
  query: string,
  options?: {
    limit?: number;
    lang?: string;
    lat?: number;
    lon?: number;
    bbox?: [number, number, number, number];
  }
): Promise<GeocodingResult> {
  const params = new URLSearchParams({ q: query });

  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.lang) params.set("lang", options.lang);
  if (options?.lat) params.set("lat", String(options.lat));
  if (options?.lon) params.set("lon", String(options.lon));
  if (options?.bbox) {
    const [w, s, e, n] = options.bbox;
    params.set("bbox", `${w},${s},${e},${n}`);
  }

  const response = await fetch(`${PHOTON_URL}/api?${params}`);

  if (!response.ok) {
    throw new Error(`Geocoding failed: ${response.statusText}`);
  }

  const data = await response.json();
  return PhotonResponseSchema.parse(data);
}

export async function reverseGeocode(
  lat: number,
  lon: number,
  options?: { radius?: number; limit?: number; lang?: string }
): Promise<GeocodingResult> {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
  });

  if (options?.radius) params.set("radius", String(options.radius));
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.lang) params.set("lang", options.lang);

  const response = await fetch(`${PHOTON_URL}/reverse?${params}`);

  if (!response.ok) {
    throw new Error(`Reverse geocoding failed: ${response.statusText}`);
  }

  const data = await response.json();
  return PhotonResponseSchema.parse(data);
}
