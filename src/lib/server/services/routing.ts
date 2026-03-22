import { z } from "zod";

const VALHALLA_URL = process.env.VALHALLA_URL || "http://localhost:8002";

export const locationSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
});

export const routeRequestSchema = z.object({
  locations: z.array(locationSchema).min(2),
  costing: z.enum(["auto", "bicycle", "pedestrian", "truck"]),
  directions_options: z
    .object({ units: z.enum(["miles", "kilometers"]) })
    .optional(),
  alternates: z.number().int().min(0).max(3).optional(),
});

export const isochroneRequestSchema = z.object({
  locations: z.array(locationSchema).min(1),
  costing: z.enum(["auto", "bicycle", "pedestrian"]),
  contours: z.array(
    z.object({ time: z.number().positive(), color: z.string().optional() })
  ),
  polygons: z.boolean(),
});

export const matrixRequestSchema = z.object({
  sources: z.array(locationSchema).min(1),
  targets: z.array(locationSchema).min(1),
  costing: z.string().default("auto"),
});

export type RouteRequest = z.infer<typeof routeRequestSchema>;
export type IsochroneRequest = z.infer<typeof isochroneRequestSchema>;
export type MatrixRequest = z.infer<typeof matrixRequestSchema>;

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

export interface RouteResult {
  routes: DecodedRoute[];
  rawResponse: unknown;
}

function decodePolyline(encoded: string, precision = 6): [number, number][] {
  const factor = Math.pow(10, precision);
  const coordinates: [number, number][] = [];
  let index = 0;
  let lat = 0;
  let lon = 0;

  while (index < encoded.length) {
    let b: number;
    let shift = 0;
    let result = 0;

    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);

    const dlat = result & 1 ? ~(result >> 1) : result >> 1;
    lat += dlat;

    shift = 0;
    result = 0;

    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);

    const dlon = result & 1 ? ~(result >> 1) : result >> 1;
    lon += dlon;

    coordinates.push([lon / factor, lat / factor]);
  }

  return coordinates;
}

function extractManeuvers(legs: unknown[]): Maneuver[] {
  const maneuvers: Maneuver[] = [];
  for (const leg of legs) {
    const legObj = leg as Record<string, unknown>;
    const legManeuvers = legObj.maneuvers as Array<Record<string, unknown>>;
    if (!Array.isArray(legManeuvers)) continue;
    for (const m of legManeuvers) {
      maneuvers.push({
        type: (m.type as number) ?? 0,
        instruction: (m.instruction as string) ?? "",
        distance: (m.length as number) ?? 0,
        time: (m.time as number) ?? 0,
        beginShapeIndex: (m.begin_shape_index as number) ?? 0,
        endShapeIndex: (m.end_shape_index as number) ?? 0,
      });
    }
  }
  return maneuvers;
}

function buildDecodedRoute(trip: Record<string, unknown>): DecodedRoute {
  const shape = trip.shape as string;
  const legs = (trip.legs as unknown[]) ?? [];
  const summary = trip.summary as Record<string, unknown>;

  const coordinates = decodePolyline(shape);
  const maneuvers = extractManeuvers(legs);

  return {
    geometry: {
      type: "LineString",
      coordinates,
    },
    maneuvers,
    summary: {
      length: (summary?.length as number) ?? 0,
      time: (summary?.time as number) ?? 0,
      hasHighway: (summary?.has_highway as boolean) ?? false,
      hasToll: (summary?.has_toll as boolean) ?? false,
    },
  };
}

export async function getRoute(request: RouteRequest): Promise<RouteResult> {
  const validated = routeRequestSchema.parse(request);
  const response = await fetch(`${VALHALLA_URL}/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(validated),
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => response.statusText);
    throw new Error(`Routing failed: ${errText}`);
  }

  const raw = (await response.json()) as Record<string, unknown>;

  const routes: DecodedRoute[] = [];

  if (raw.trip) {
    routes.push(buildDecodedRoute(raw.trip as Record<string, unknown>));
  }

  if (Array.isArray(raw.alternates)) {
    for (const alt of raw.alternates as Array<Record<string, unknown>>) {
      if (alt.trip) {
        routes.push(buildDecodedRoute(alt.trip as Record<string, unknown>));
      }
    }
  }

  return { routes, rawResponse: raw };
}

export async function getIsochrone(request: IsochroneRequest) {
  const validated = isochroneRequestSchema.parse(request);
  const response = await fetch(`${VALHALLA_URL}/isochrone`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(validated),
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => response.statusText);
    throw new Error(`Isochrone failed: ${errText}`);
  }

  return response.json();
}

export async function getMatrix(
  sources: Array<{ lat: number; lon: number }>,
  targets: Array<{ lat: number; lon: number }>,
  costing = "auto"
) {
  const validated = matrixRequestSchema.parse({ sources, targets, costing });
  const response = await fetch(`${VALHALLA_URL}/sources_to_targets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sources: validated.sources.map((s) => ({ lat: s.lat, lon: s.lon })),
      targets: validated.targets.map((t) => ({ lat: t.lat, lon: t.lon })),
      costing: validated.costing,
    }),
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => response.statusText);
    throw new Error(`Matrix failed: ${errText}`);
  }

  return response.json();
}
