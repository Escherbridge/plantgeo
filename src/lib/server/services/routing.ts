import { z } from "zod";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";

// Formerly `@/lib/routing`, deleted 2026-10-25: DirectionsList/RouteSummary were its only
// other consumers and both are gone, so these types now live where their one remaining
// reader (this file) uses them (dead-code audit).
export interface Maneuver {
  type: number;
  instruction: string;
  distance: number;
  time: number;
  beginShapeIndex: number;
  endShapeIndex: number;
}

export interface DecodedRoute {
  geometry: GeoJSON.LineString;
  maneuvers: Maneuver[];
  summary: {
    length: number;
    time: number;
    hasHighway: boolean;
    hasToll: boolean;
  };
}

const ROUTING_TIMEOUT_MS = 8_000;
const MAX_ROUTING_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_DECODED_ROUTE_POINTS = 100_000;

export const locationSchema = z
  .object({
    lat: z.number().finite().min(-90).max(90),
    lon: z.number().finite().min(-180).max(180),
  })
  .strict();

export const routeRequestSchema = z
  .object({
    locations: z.array(locationSchema).min(2).max(25),
    costing: z.enum(["auto", "bicycle", "pedestrian", "truck"]),
    directions_options: z
      .object({ units: z.enum(["miles", "kilometers"]) })
      .strict()
      .optional(),
    alternates: z.number().int().min(0).max(3).optional(),
  })
  .strict();

const ManeuverResponseSchema = z
  .object({
    type: z.number().int().min(0).max(255).optional(),
    instruction: z.string().max(2_000).optional(),
    length: z.number().finite().nonnegative().max(100_000).optional(),
    time: z.number().finite().nonnegative().max(31_536_000).optional(),
    begin_shape_index: z.number().int().nonnegative().max(MAX_DECODED_ROUTE_POINTS).optional(),
    end_shape_index: z.number().int().nonnegative().max(MAX_DECODED_ROUTE_POINTS).optional(),
  })
  .strip();

const TripResponseSchema = z
  .object({
    shape: z.string().min(1).max(1_000_000),
    legs: z
      .array(
        z
          .object({ maneuvers: z.array(ManeuverResponseSchema).max(2_000).optional() })
          .strip()
      )
      .max(25)
      .default([]),
    summary: z
      .object({
        length: z.number().finite().nonnegative().max(100_000).optional(),
        time: z.number().finite().nonnegative().max(31_536_000).optional(),
        has_highway: z.boolean().optional(),
        has_toll: z.boolean().optional(),
      })
      .strip()
      .default({}),
  })
  .strip();

const ValhallaRouteResponseSchema = z
  .object({
    trip: TripResponseSchema.optional(),
    alternates: z
      .array(z.object({ trip: TripResponseSchema.optional() }).strip())
      .max(3)
      .optional(),
  })
  .strip()
  .refine((value) => Boolean(value.trip), { message: "Route response did not contain a trip" });

export type RouteRequest = z.infer<typeof routeRequestSchema>;

export interface RouteResult {
  routes: DecodedRoute[];
  rawResponse: z.infer<typeof ValhallaRouteResponseSchema>;
}

function endpoint(path: "/route"): URL {
  const url = providerUrl("VALHALLA_URL", "http://localhost:8002");
  url.pathname = `${url.pathname.replace(/\/$/, "")}${path}`;
  return url;
}

async function requestValhalla(path: Parameters<typeof endpoint>[0], body: unknown): Promise<unknown> {
  return fetchBoundedJson(
    endpoint(path),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    { maxBytes: MAX_ROUTING_RESPONSE_BYTES, timeoutMs: ROUTING_TIMEOUT_MS }
  );
}

function decodePolyline(encoded: string, precision = 6): [number, number][] {
  const factor = 10 ** precision;
  const coordinates: [number, number][] = [];
  let index = 0;
  let lat = 0;
  let lon = 0;

  const readDelta = (): number => {
    let shift = 0;
    let result = 0;
    while (true) {
      if (index >= encoded.length || shift > 30) {
        throw new UpstreamPayloadError("Route shape was malformed");
      }
      const value = encoded.charCodeAt(index) - 63;
      index += 1;
      if (value < 0 || value > 63) throw new UpstreamPayloadError("Route shape was malformed");
      result |= (value & 0x1f) << shift;
      if (value < 0x20) break;
      shift += 5;
    }
    return result & 1 ? ~(result >> 1) : result >> 1;
  };

  while (index < encoded.length) {
    lat += readDelta();
    lon += readDelta();
    const latitude = lat / factor;
    const longitude = lon / factor;
    if (
      !Number.isFinite(latitude) ||
      !Number.isFinite(longitude) ||
      latitude < -90 ||
      latitude > 90 ||
      longitude < -180 ||
      longitude > 180
    ) {
      throw new UpstreamPayloadError("Route shape contained an invalid coordinate");
    }
    coordinates.push([longitude, latitude]);
    if (coordinates.length > MAX_DECODED_ROUTE_POINTS) {
      throw new UpstreamPayloadError("Route shape exceeded the coordinate limit");
    }
  }
  if (coordinates.length < 2) throw new UpstreamPayloadError("Route shape was empty");
  return coordinates;
}

function buildDecodedRoute(trip: z.infer<typeof TripResponseSchema>): DecodedRoute {
  const maneuvers: Maneuver[] = trip.legs.flatMap((leg) =>
    (leg.maneuvers ?? []).map((maneuver) => ({
      type: maneuver.type ?? 0,
      instruction: maneuver.instruction ?? "",
      distance: maneuver.length ?? 0,
      time: maneuver.time ?? 0,
      beginShapeIndex: maneuver.begin_shape_index ?? 0,
      endShapeIndex: maneuver.end_shape_index ?? 0,
    }))
  );

  return {
    geometry: { type: "LineString", coordinates: decodePolyline(trip.shape) },
    maneuvers,
    summary: {
      length: trip.summary.length ?? 0,
      time: trip.summary.time ?? 0,
      hasHighway: trip.summary.has_highway ?? false,
      hasToll: trip.summary.has_toll ?? false,
    },
  };
}

export async function getRoute(request: RouteRequest): Promise<RouteResult> {
  const validated = routeRequestSchema.parse(request);
  const raw = await requestValhalla("/route", validated);
  const parsed = ValhallaRouteResponseSchema.safeParse(raw);
  if (!parsed.success) throw new UpstreamPayloadError("Valhalla returned an invalid route");

  const routes = [buildDecodedRoute(parsed.data.trip!)];
  for (const alternate of parsed.data.alternates ?? []) {
    if (alternate.trip) routes.push(buildDecodedRoute(alternate.trip));
  }
  return { routes, rawResponse: parsed.data };
}
