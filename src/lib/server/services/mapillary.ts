import { z } from "zod";
import type { MapillaryImage } from "@/lib/mapillary";
import {
  fetchBoundedJson,
  UpstreamConfigurationError,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";

const MAPILLARY_API_URL = "https://graph.mapillary.com";
const MAX_MAPILLARY_RESPONSE_BYTES = 1024 * 1024;
const MAPILLARY_TIMEOUT_MS = 7_000;
const MapillaryIdSchema = z.string().trim().regex(/^[A-Za-z0-9_-]{1,128}$/);

const MapillaryApiImageSchema = z
  .object({
    id: MapillaryIdSchema,
    geometry: z
      .object({
        type: z.literal("Point"),
        coordinates: z.tuple([
          z.number().finite().min(-180).max(180),
          z.number().finite().min(-90).max(90),
        ]),
      })
      .strict(),
    thumb_1024_url: z.string().url().max(2_048).optional(),
    thumb_2048_url: z.string().url().max(2_048).optional(),
    compass_angle: z.number().finite().min(0).max(360).optional(),
    sequence: MapillaryIdSchema.optional(),
    sequence_id: MapillaryIdSchema.optional(),
  })
  .strip();

const MapillaryApiResponseSchema = z
  .object({ data: z.array(MapillaryApiImageSchema).max(200) })
  .strip();

export const MapillaryBboxQuerySchema = z
  .object({
    west: z.coerce.number().finite().min(-180).max(180),
    south: z.coerce.number().finite().min(-90).max(90),
    east: z.coerce.number().finite().min(-180).max(180),
    north: z.coerce.number().finite().min(-90).max(90),
    limit: z.coerce.number().int().min(1).max(100).default(100),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.west >= value.east || value.south >= value.north) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "bbox must have positive area" });
    }
    if ((value.east - value.west) * (value.north - value.south) > 25) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "bbox is too large" });
    }
  });

export const MapillaryImageQuerySchema = z.object({ id: MapillaryIdSchema }).strict();
export const MapillarySequenceQuerySchema = z
  .object({ sequenceId: MapillaryIdSchema })
  .strict();

export type { MapillaryImage } from "@/lib/mapillary";

function accessToken(): string {
  const token = process.env.MAPILLARY_ACCESS_TOKEN?.trim();
  if (!token) throw new UpstreamConfigurationError("MAPILLARY_ACCESS_TOKEN is not configured");
  return token;
}

async function requestMapillary(url: URL): Promise<unknown> {
  return fetchBoundedJson(
    url,
    { method: "GET", headers: { Authorization: `OAuth ${accessToken()}` } },
    { maxBytes: MAX_MAPILLARY_RESPONSE_BYTES, timeoutMs: MAPILLARY_TIMEOUT_MS }
  );
}

function toMapillaryImage(
  raw: z.infer<typeof MapillaryApiImageSchema>,
  preferHigh = false
): MapillaryImage {
  return {
    id: raw.id,
    geometry: { type: "Point", coordinates: raw.geometry.coordinates },
    thumbUrl:
      (preferHigh ? raw.thumb_2048_url : raw.thumb_1024_url) ||
      raw.thumb_1024_url ||
      raw.thumb_2048_url ||
      "",
    compassAngle: raw.compass_angle ?? 0,
    sequenceId: raw.sequence_id ?? raw.sequence ?? "",
  };
}

export async function getImages(
  bbox: { west: number; south: number; east: number; north: number },
  limit = 100
): Promise<GeoJSON.FeatureCollection<GeoJSON.Point>> {
  const validated = MapillaryBboxQuerySchema.parse({ ...bbox, limit });
  const url = new URL("/images", MAPILLARY_API_URL);
  url.searchParams.set("fields", "id,geometry,thumb_1024_url,compass_angle,sequence");
  url.searchParams.set(
    "bbox",
    `${validated.west},${validated.south},${validated.east},${validated.north}`
  );
  url.searchParams.set("limit", String(validated.limit));

  const raw = await requestMapillary(url);
  const parsed = MapillaryApiResponseSchema.safeParse(raw);
  if (!parsed.success) throw new UpstreamPayloadError("Mapillary returned an invalid image list");
  return {
    type: "FeatureCollection",
    features: parsed.data.data.map((image) => ({
      type: "Feature",
      properties: {
        id: image.id,
        thumbUrl: image.thumb_1024_url || "",
        compassAngle: image.compass_angle ?? 0,
        sequenceId: image.sequence_id ?? image.sequence ?? "",
      },
      geometry: { type: "Point", coordinates: image.geometry.coordinates },
    })),
  };
}

export async function getImageById(id: string): Promise<MapillaryImage> {
  const validatedId = MapillaryIdSchema.parse(id);
  const url = new URL(`/${validatedId}`, MAPILLARY_API_URL);
  url.searchParams.set("fields", "id,geometry,thumb_2048_url,compass_angle,sequence_id");
  const raw = await requestMapillary(url);
  const parsed = MapillaryApiImageSchema.safeParse(raw);
  if (!parsed.success) throw new UpstreamPayloadError("Mapillary returned an invalid image");
  return toMapillaryImage(parsed.data, true);
}

export async function getSequence(sequenceId: string): Promise<MapillaryImage[]> {
  const validatedId = MapillaryIdSchema.parse(sequenceId);
  const url = new URL("/images", MAPILLARY_API_URL);
  url.searchParams.set("fields", "id,geometry,thumb_1024_url,compass_angle,sequence_id");
  url.searchParams.set("sequence_id", validatedId);
  url.searchParams.set("limit", "200");
  const raw = await requestMapillary(url);
  const parsed = MapillaryApiResponseSchema.safeParse(raw);
  if (!parsed.success) throw new UpstreamPayloadError("Mapillary returned an invalid sequence");
  return parsed.data.data.map((image) => toMapillaryImage(image));
}
