import { z } from "zod";
import {
  fetchBoundedJson,
  UpstreamHttpError,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";

export interface WfigsFirePerimeter {
  uniqueFireIdentifier: string;
  irwinId: string | null;
  incidentName: string | null;
  fireDiscoveryDateTime: string | null;
  polygonDateTime: string | null;
  gisAcres: number | null;
  fireCause: string | null;
  incidentTypeCategory: string | null;
  pooState: string | null;
  percentContained: number | null;
  geometry:
    | { type: "Polygon"; coordinates: number[][][] }
    | { type: "MultiPolygon"; coordinates: number[][][][] };
}

export interface WfigsFirePerimeterCollection {
  features: WfigsFirePerimeter[];
}

const QUERY_URL =
  "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query";
const OUT_FIELDS = [
  "attr_UniqueFireIdentifier",
  "attr_IrwinID",
  "poly_IncidentName",
  "attr_FireDiscoveryDateTime",
  "poly_GISAcres",
  "attr_FireCause",
  "poly_PolygonDateTime",
  "attr_IncidentTypeCategory",
  "attr_POOState",
  "attr_PercentContained",
].join(",");
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 20_000;
const MAX_RECORD_COUNT = 2_000;

const RingSchema = z.array(z.array(z.number().finite()).min(2).max(3));

const WfigsGeometrySchema = z.union([
  z.object({ type: z.literal("Polygon"), coordinates: z.array(RingSchema) }),
  z.object({
    type: z.literal("MultiPolygon"),
    coordinates: z.array(z.array(RingSchema)),
  }),
]);

const WfigsFeatureSchema = z.object({
  type: z.literal("Feature"),
  geometry: WfigsGeometrySchema,
  properties: z
    .object({
      attr_UniqueFireIdentifier: z.string().min(1),
      attr_IrwinID: z.string().nullable().optional(),
      poly_IncidentName: z.string().nullable().optional(),
      attr_FireDiscoveryDateTime: z.number().finite().nullable().optional(),
      poly_GISAcres: z.number().finite().nullable().optional(),
      attr_FireCause: z.string().nullable().optional(),
      poly_PolygonDateTime: z.number().finite().nullable().optional(),
      attr_IncidentTypeCategory: z.string().nullable().optional(),
      attr_POOState: z.string().nullable().optional(),
      attr_PercentContained: z.number().finite().nullable().optional(),
    })
    .passthrough(),
});

const WfigsFeatureCollectionSchema = z
  .object({
    type: z.literal("FeatureCollection"),
    features: z.array(WfigsFeatureSchema),
  })
  .passthrough();

const WfigsErrorSchema = z.object({
  error: z.object({
    code: z.number().optional(),
    message: z.string().optional(),
    details: z.array(z.string()).optional(),
  }),
});

const MAX_ATTEMPTS = 3;
const RETRY_BASE_DELAYS_MS = [1_000, 2_000];
const BUSY_MESSAGE_PATTERN = /too many requests|busy|try again/i;

/** Resolve after `ms`, wrapping setTimeout as an awaitable promise. */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Exponential backoff base delay for `attemptIndex`, randomized by a 0.5-1.5x jitter. */
function jitteredRetryDelayMs(attemptIndex: number): number {
  return RETRY_BASE_DELAYS_MS[attemptIndex] * (0.5 + Math.random());
}

/** True when ArcGIS reported its own busy/throttling error instead of an HTTP status. */
function isBusyPayloadError(error: unknown): boolean {
  return error instanceof UpstreamPayloadError && BUSY_MESSAGE_PATTERN.test(error.message);
}

/** True for a transient upstream HTTP failure (429 or 5xx) worth retrying. */
function isRetryableHttpError(error: unknown): boolean {
  return error instanceof UpstreamHttpError && (error.status === 429 || error.status >= 500);
}

function epochMsToIso(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

function buildQueryUrl(bbox: string, maxRecordCount: number): URL {
  const url = new URL(QUERY_URL);
  url.search = new URLSearchParams({
    where: "1=1",
    outFields: OUT_FIELDS,
    geometry: bbox,
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    outSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    resultRecordCount: String(maxRecordCount),
    f: "geojson",
  }).toString();
  return url;
}

/** One query attempt: fetch, reject ArcGIS busy payloads, then parse the feature collection. */
async function requestWfigsFirePerimeters(
  url: URL
): Promise<WfigsFirePerimeterCollection> {
  const payload = await fetchBoundedJson(
    url,
    { method: "GET" },
    { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS }
  );

  const errorShape = WfigsErrorSchema.safeParse(payload);
  if (errorShape.success) {
    throw new UpstreamPayloadError(
      `WFIGS API error: ${errorShape.data.error.message || errorShape.data.error.details?.join("; ") || "unknown"}`
    );
  }

  const parsed = WfigsFeatureCollectionSchema.safeParse(payload);
  if (!parsed.success) {
    throw new UpstreamPayloadError(
      "WFIGS API returned an unexpected feature collection shape"
    );
  }

  return {
    features: parsed.data.features.map((feature) => {
      const properties = feature.properties;
      return {
        uniqueFireIdentifier: properties.attr_UniqueFireIdentifier,
        irwinId: properties.attr_IrwinID ?? null,
        incidentName: properties.poly_IncidentName ?? null,
        fireDiscoveryDateTime: epochMsToIso(properties.attr_FireDiscoveryDateTime),
        polygonDateTime: epochMsToIso(properties.poly_PolygonDateTime),
        gisAcres: properties.poly_GISAcres ?? null,
        fireCause: properties.attr_FireCause ?? null,
        incidentTypeCategory: properties.attr_IncidentTypeCategory ?? null,
        pooState: properties.attr_POOState ?? null,
        percentContained: properties.attr_PercentContained ?? null,
        geometry: feature.geometry,
      };
    }),
  };
}

/**
 * Fetch bounded WFIGS Interagency Fire Perimeters (current, non-contained incidents)
 * from the public NIFC ArcGIS FeatureServer. No API key required.
 * Retries up to {@link MAX_ATTEMPTS} times with jittered backoff when the
 * service reports itself busy (HTTP-200 error payload) or returns 429/5xx;
 * every other failure throws immediately.
 * @param bbox "west,south,east,north"
 */
export async function fetchWfigsFirePerimeters(
  bbox: string
): Promise<WfigsFirePerimeterCollection> {
  const url = buildQueryUrl(bbox, MAX_RECORD_COUNT);

  let lastError: unknown;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    try {
      return await requestWfigsFirePerimeters(url);
    } catch (error) {
      lastError = error;
      const isLastAttempt = attempt === MAX_ATTEMPTS - 1;
      const isRetryable = isBusyPayloadError(error) || isRetryableHttpError(error);
      if (isLastAttempt || !isRetryable) throw error;
      await sleep(jitteredRetryDelayMs(attempt));
    }
  }
  // Unreachable: the loop above always returns or throws.
  throw lastError;
}
