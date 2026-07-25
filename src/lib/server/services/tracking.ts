import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import { eq, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { assets, positions } from "@/lib/server/db/schema";

export interface TrackingPositionInput {
  assetId: string;
  lat: number;
  lon: number;
  heading?: number;
  speed?: number;
  altitude?: number;
  producerTimestamp: Date;
}

export class UnknownTrackingAssetError extends Error {}
export class DuplicateTrackingPositionError extends Error {}

/** Verify the asset and durably insert one idempotent position in one transaction. */
export async function persistVerifiedPosition(
  input: TrackingPositionInput
): Promise<{ producerTimestamp: Date; receivedAt: Date }> {
  const receivedAt = new Date();
  return db.transaction(async (transaction) => {
    const asset = await transaction
      .select({ id: assets.id })
      .from(assets)
      .where(eq(assets.id, input.assetId))
      .limit(1);
    if (!asset[0]) throw new UnknownTrackingAssetError("Tracking asset does not exist");

    const inserted = await transaction
      .insert(positions)
      .values({
        time: input.producerTimestamp,
        assetId: input.assetId,
        heading: input.heading ?? null,
        speed: input.speed ?? null,
        altitude: input.altitude ?? null,
        metadata: {
          lat: input.lat,
          lon: input.lon,
          receivedAt: receivedAt.toISOString(),
        },
      })
      .onConflictDoNothing({ target: [positions.assetId, positions.time] })
      .returning({ time: positions.time });
    if (!inserted[0]) {
      throw new DuplicateTrackingPositionError("Tracking position already exists");
    }
    return { producerTimestamp: inserted[0].time, receivedAt };
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
