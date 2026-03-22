import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import { sql } from "drizzle-orm";
import { geofences, alerts } from "@/lib/server/db/schema";

interface GeofenceRow {
  id: string;
  name: string;
  geometry: GeoJSON.Polygon;
  alertOnEnter: boolean | null;
  alertOnExit: boolean | null;
}

function pointInPolygon(
  lat: number,
  lon: number,
  polygon: GeoJSON.Polygon
): boolean {
  const coords = polygon.coordinates[0];
  let inside = false;
  for (let i = 0, j = coords.length - 1; i < coords.length; j = i++) {
    const xi = coords[i][0];
    const yi = coords[i][1];
    const xj = coords[j][0];
    const yj = coords[j][1];
    const intersect =
      yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

export async function checkGeofences(
  db: PostgresJsDatabase,
  assetId: string,
  prevLat: number,
  prevLon: number,
  currLat: number,
  currLon: number
): Promise<void> {
  const rows = await db
    .select()
    .from(geofences) as GeofenceRow[];

  for (const fence of rows) {
    if (!fence.geometry?.coordinates) continue;

    const wasInside = pointInPolygon(prevLat, prevLon, fence.geometry);
    const isInside = pointInPolygon(currLat, currLon, fence.geometry);

    if (!wasInside && isInside && fence.alertOnEnter) {
      await db.insert(alerts).values({
        assetId,
        geofenceId: fence.id,
        type: "geofence_breach",
        message: `Asset entered geofence "${fence.name}"`,
        metadata: { event: "enter", geofenceName: fence.name },
      });
    } else if (wasInside && !isInside && fence.alertOnExit) {
      await db.insert(alerts).values({
        assetId,
        geofenceId: fence.id,
        type: "geofence_breach",
        message: `Asset exited geofence "${fence.name}"`,
        metadata: { event: "exit", geofenceName: fence.name },
      });
    }
  }
}
