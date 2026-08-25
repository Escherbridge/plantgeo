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

interface TrackingPositionRow {
  asset_id: string;
  time: Date;
  heading: number | null;
  speed: number | null;
  altitude: number | null;
  metadata: { lat: number; lon: number };
}

/**
 * `db.execute` on postgres-js resolves to a RowList, which IS the array; only older driver
 * shapes wrap it in `.rows`. Both readers below reached straight for `.rows` and cast, so on
 * this stack (drizzle 0.39 + postgres-js) they returned `undefined` rather than rows -- and
 * because the cast asserted the array type, neither the compiler nor the one test that reaches
 * `getRouteHistory` (it asserts only that a non-admin is refused) could see it.
 */
function resultRows(result: unknown): TrackingPositionRow[] {
  if (Array.isArray(result)) return result as TrackingPositionRow[];
  const wrapped = result as { rows?: unknown } | null;
  return Array.isArray(wrapped?.rows) ? (wrapped.rows as TrackingPositionRow[]) : [];
}

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

/**
 * How far back a "last known position" may be and still be a position.
 *
 * `tracking.positions` was formerly a TimescaleDB hypertable (removed 2026-08-25 because it
 * held 0 chunks, 0 rows, 40 kB and no continuous aggregates). It is now an ordinary PostgreSQL
 * table. The time-window bound exists because the shape it replaces -- `DISTINCT ON (asset_id)
 * ... ORDER BY asset_id, time DESC` with no time predicate and no LIMIT -- sorts the ENTIRE
 * positions table. A live-tracking table is the one relation in this schema whose row count is
 * set by wall-clock time rather than by how much ground the ingest lanes cover. On a 3 GB box
 * that sort is the failure this whole pre-aggregation pass exists to prevent, and it would
 * arrive on the day the first fleet connects rather than on a day anyone was measuring.
 *
 * 24 hours: an asset that has not reported in a day is not "where it is", it is a stale
 * record, and the map draws a stale pin as a current one.
 */
const LAST_POSITION_MAX_AGE_HOURS = 24;

/** Assets one map read may pin. Well above any fleet this platform has served. */
const LAST_POSITION_MAX_ASSETS = 5_000;

/** Days one route request may span. Beyond this the answer is a report, not a map layer. */
const ROUTE_HISTORY_MAX_RANGE_DAYS = 7;

/** Points one route may draw. A 7-day route at 5 s resolution is 120,960 -- this caps it. */
const ROUTE_HISTORY_MAX_POINTS = 5_000;

export async function getLastPositions(db: PostgresJsDatabase) {
  const result = await db.execute(
    sql`SELECT DISTINCT ON (asset_id) asset_id, time, heading, speed, altitude, metadata
        FROM tracking.positions
        WHERE time >= now() - (${LAST_POSITION_MAX_AGE_HOURS}::integer * interval '1 hour')
        ORDER BY asset_id, time DESC
        LIMIT ${LAST_POSITION_MAX_ASSETS}`
  );
  return resultRows(result);
}

/**
 * One asset's track between two instants, with the range CLAMPED rather than refused.
 *
 * Clamped to the newest `ROUTE_HISTORY_MAX_RANGE_DAYS` of the requested window, so a caller
 * asking for a year gets the most recent week rather than an error -- the recent end is the
 * one a track is read for.
 *
 * The row cap is a hard LIMIT and this reader does NOT report having hit it: with
 * `ORDER BY time ASC` a capped answer is the OLDEST `ROUTE_HISTORY_MAX_POINTS` of the clamped
 * window, so a caller comparing `rows.length` against the cap is the only signal there is. That
 * is a deliberate stopgap, not a finished contract -- an unbounded read is strictly worse -- and
 * the honest fix is a `truncated` flag on the caller's payload, which needs the route-history
 * response type widened.
 */
export async function getRouteHistory(
  db: PostgresJsDatabase,
  assetId: string,
  from: Date,
  to: Date
) {
  const maxRangeMs = ROUTE_HISTORY_MAX_RANGE_DAYS * 86_400_000;
  const clampedFrom =
    to.getTime() - from.getTime() > maxRangeMs
      ? new Date(to.getTime() - maxRangeMs)
      : from;
  const result = await db.execute(
    sql`SELECT asset_id, time, heading, speed, altitude, metadata
        FROM tracking.positions
        WHERE asset_id = ${assetId}
          AND time BETWEEN ${clampedFrom} AND ${to}
        ORDER BY time ASC
        LIMIT ${ROUTE_HISTORY_MAX_POINTS}`
  );
  return resultRows(result);
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
