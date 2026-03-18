const VALHALLA_URL = process.env.VALHALLA_URL || "http://localhost:8002";

export interface RouteRequest {
  locations: Array<{ lat: number; lon: number }>;
  costing: "auto" | "bicycle" | "pedestrian" | "truck";
  directions_options?: { units: "miles" | "kilometers" };
}

export interface IsochroneRequest {
  locations: Array<{ lat: number; lon: number }>;
  costing: "auto" | "bicycle" | "pedestrian";
  contours: Array<{ time: number; color?: string }>;
  polygons: boolean;
}

export async function getRoute(request: RouteRequest) {
  const response = await fetch(`${VALHALLA_URL}/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`Routing failed: ${response.statusText}`);
  }

  return response.json();
}

export async function getIsochrone(request: IsochroneRequest) {
  const response = await fetch(`${VALHALLA_URL}/isochrone`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`Isochrone failed: ${response.statusText}`);
  }

  return response.json();
}

export async function getMatrix(
  sources: Array<{ lat: number; lon: number }>,
  targets: Array<{ lat: number; lon: number }>,
  costing: string = "auto"
) {
  const response = await fetch(`${VALHALLA_URL}/sources_to_targets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sources: sources.map((s) => ({ lat: s.lat, lon: s.lon })),
      targets: targets.map((t) => ({ lat: t.lat, lon: t.lon })),
      costing,
    }),
  });

  if (!response.ok) {
    throw new Error(`Matrix failed: ${response.statusText}`);
  }

  return response.json();
}
