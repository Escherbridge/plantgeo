import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import { sql } from "drizzle-orm";
import { positions } from "@/lib/server/db/schema";

export async function storePosition(
  db: PostgresJsDatabase,
  assetId: string,
  lat: number,
  lon: number,
  heading?: number,
  speed?: number,
  altitude?: number
): Promise<void> {
  await db.insert(positions).values({
    time: new Date(),
    assetId,
    heading: heading ?? null,
    speed: speed ?? null,
    altitude: altitude ?? null,
    metadata: { lat, lon },
  });
}

export async function getLastPositions(db: PostgresJsDatabase) {
  const result = await db.execute(
    sql`SELECT DISTINCT ON (asset_id) asset_id, time, heading, speed, altitude, metadata
        FROM tracking.positions
        ORDER BY asset_id, time DESC`
  );
  return (result as unknown as { rows: unknown[] }).rows as Array<{
    asset_id: string;
    time: Date;
    heading: number | null;
    speed: number | null;
    altitude: number | null;
    metadata: { lat: number; lon: number };
  }>;
}

export async function getRouteHistory(
  db: PostgresJsDatabase,
  assetId: string,
  from: Date,
  to: Date
) {
  const result = await db.execute(
    sql`SELECT asset_id, time, heading, speed, altitude, metadata
        FROM tracking.positions
        WHERE asset_id = ${assetId}
          AND time BETWEEN ${from} AND ${to}
        ORDER BY time ASC`
  );
  return (result as unknown as { rows: unknown[] }).rows as Array<{
    asset_id: string;
    time: Date;
    heading: number | null;
    speed: number | null;
    altitude: number | null;
    metadata: { lat: number; lon: number };
  }>;
}

interface PositionRow {
  time: Date;
  metadata: { lat: number; lon: number };
}

interface Stop {
  lat: number;
  lon: number;
  startTime: Date;
  endTime: Date;
  durationMs: number;
}

export function detectStops(positions: PositionRow[]): Stop[] {
  const STOP_THRESHOLD_MS = 5 * 60 * 1000;
  const stops: Stop[] = [];

  for (let i = 1; i < positions.length; i++) {
    const prev = positions[i - 1];
    const curr = positions[i];
    const gap = curr.time.getTime() - prev.time.getTime();
    if (gap > STOP_THRESHOLD_MS) {
      stops.push({
        lat: prev.metadata.lat,
        lon: prev.metadata.lon,
        startTime: prev.time,
        endTime: curr.time,
        durationMs: gap,
      });
    }
  }

  return stops;
}
