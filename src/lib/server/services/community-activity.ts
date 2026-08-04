/** Server-side aggregates over community strategy requests. See `src/lib/server/AGENTS.md` §community-activity. */

import { and, eq, isNull, or, sql, type SQL } from "drizzle-orm";
import type { db } from "@/lib/server/db";
import { strategyRequests, teamMembers } from "@/lib/server/db/schema";
import type { BoundingBox } from "@/lib/server/security/bbox";

type Database = typeof db;

/** Coarsest cell, so a whole-globe viewport still groups. */
const MAXIMUM_CELL_DEGREES = 10;

/** Finest cell (~1.1 km at the equator) — the generalization floor for published cells. */
const MINIMUM_CELL_DEGREES = 0.01;

/**
 * Fewest contributing requests a cell may disclose. A cell of one is not an aggregate — it
 * resolves a single private submission to the cell centre, which the coarse-cell floor alone
 * does not prevent. Callers may raise this, never lower it. See src/lib/server/AGENTS.md.
 */
const MINIMUM_CELL_MEMBERS = 3;

export interface ActivityCell {
  longitude: number;
  latitude: number;
  featureCount: number;
  voteCount: number;
}

export interface ActivityGrid {
  cells: ActivityCell[];
  cellDegrees: number;
  /** Newest request contributing to the returned cells; null when the grid is empty. */
  observedAt: Date | null;
}

export interface ActivityGridOptions {
  boundingBox?: BoundingBox;
  zoom: number;
  limit: number;
  minimumVotes: number;
  minimumFeatureCount: number;
}

export interface StrategyActivitySummary {
  id: string;
  strategyType: string;
  requestCount: number;
  totalVotes: number;
}

/** Degrees per aggregation cell at a zoom level, clamped to the generalization floor. */
export function gridCellDegrees(zoom: number): number {
  const clampedZoom = Math.min(22, Math.max(0, zoom));
  const cellDegrees = 360 / 2 ** (clampedZoom + 3);
  return Math.min(
    MAXIMUM_CELL_DEGREES,
    Math.max(MINIMUM_CELL_DEGREES, cellDegrees)
  );
}

/** The whole cells a viewport covers, plus a deliberately over-wide sargable coordinate window. */
export interface ActivityCellRange {
  /** Inclusive cell-index bounds, in the units `floor(coordinate / cellDegrees)` produces. */
  westCell: number;
  eastCell: number;
  southCell: number;
  northCell: number;
  /** Coordinate bounds one whole cell beyond the index bounds, so they can never trim a kept cell. */
  minimumLongitude: number;
  maximumLongitude: number;
  minimumLatitude: number;
  maximumLatitude: number;
}

/**
 * Widen a caller's viewport to the whole cells it touches.
 *
 * This is the k-anonymity rule, not a rounding convenience. `MINIMUM_CELL_MEMBERS` is enforced as
 * a `HAVING count(*) >= 3` over the grouped rows, so if the bbox were pushed into the `WHERE` a
 * caller could shrink it and difference two overlapping answers: count(R) minus count(R minus a
 * thin strip) reveals whether one private submission lies in the strip, and about 40 requests per
 * axis resolve a contributor's exact submitted point — far below the 0.01° cell floor the floor
 * was supposed to guarantee. Deriving cell INDEX bounds with the same `floor()` the `GROUP BY`
 * uses makes a cell's membership independent of the viewport that asked for it.
 */
export function activityCellRange(
  boundingBox: BoundingBox,
  cellDegrees: number
): ActivityCellRange {
  const [west, south, east, north] = boundingBox;
  const westCell = Math.floor(west / cellDegrees);
  const eastCell = Math.floor(east / cellDegrees);
  const southCell = Math.floor(south / cellDegrees);
  const northCell = Math.floor(north / cellDegrees);
  return {
    westCell,
    eastCell,
    southCell,
    northCell,
    minimumLongitude: (westCell - 1) * cellDegrees,
    maximumLongitude: (eastCell + 2) * cellDegrees,
    minimumLatitude: (southCell - 1) * cellDegrees,
    maximumLatitude: (northCell + 2) * cellDegrees,
  };
}

function normalizeTimestamp(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "string") {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

/**
 * Groups strategy requests into a zoom-derived grid and returns cell centres with counts.
 * Never returns a request identifier, author, title, or its exact submitted point.
 *
 * The caller's bbox selects WHICH cells are returned and never which rows compose one, so a
 * cell's featureCount/voteCount are the same numbers whatever viewport asked for them. That,
 * together with MINIMUM_CELL_MEMBERS, is what makes the aggregate non-differenceable; the
 * cell-size floor alone never was. `minimumVotes`/`minimumFeatureCount` stay safe under
 * bisection for the same reason: they can only include or exclude a whole cell, and both of
 * that cell's totals are already published in its own properties.
 */
export async function aggregateActivityGrid(
  database: Database,
  options: ActivityGridOptions
): Promise<ActivityGrid> {
  const cellDegrees = gridCellDegrees(options.zoom);
  const conditions: SQL[] = [];

  if (options.boundingBox) {
    // Whole cells only -- see activityCellRange. The BETWEEN bounds are the real filter and use
    // the identical floor() expression the GROUP BY below uses, so no rounding can split a cell;
    // the four coordinate comparisons are a deliberately over-wide sargable pre-filter. Do NOT
    // replace the index bounds with a snapped coordinate range: a snapped literal and floor() can
    // disagree at a cell boundary, which is the same differencing hole in a smaller costume.
    const range = activityCellRange(options.boundingBox, cellDegrees);
    conditions.push(
      sql`${strategyRequests.lon} >= ${range.minimumLongitude}`,
      sql`${strategyRequests.lon} <= ${range.maximumLongitude}`,
      sql`${strategyRequests.lat} >= ${range.minimumLatitude}`,
      sql`${strategyRequests.lat} <= ${range.maximumLatitude}`,
      sql`floor(${strategyRequests.lon} / ${cellDegrees}) BETWEEN ${range.westCell} AND ${range.eastCell}`,
      sql`floor(${strategyRequests.lat} / ${cellDegrees}) BETWEEN ${range.southCell} AND ${range.northCell}`
    );
  }

  // Grouping and ordering use select-list ordinals so the grid expression is
  // written once; repeating a parameterized expression is not GROUP BY-safe.
  const rows = await database
    .select({
      longitudeIndex: sql<number>`floor(${strategyRequests.lon} / ${cellDegrees})::int`,
      latitudeIndex: sql<number>`floor(${strategyRequests.lat} / ${cellDegrees})::int`,
      featureCount: sql<number>`count(*)::int`,
      voteCount: sql<number>`coalesce(sum(${strategyRequests.voteCount}), 0)::int`,
      newestCreatedAt: sql<unknown>`max(${strategyRequests.createdAt})`,
    })
    .from(strategyRequests)
    .where(conditions.length > 0 ? and(...conditions) : undefined)
    .groupBy(sql`1`, sql`2`)
    .having(
      sql`count(*) >= ${Math.max(MINIMUM_CELL_MEMBERS, options.minimumFeatureCount)} and coalesce(sum(${strategyRequests.voteCount}), 0) >= ${options.minimumVotes}`
    )
    .orderBy(sql`3 desc`, sql`1`, sql`2`)
    .limit(options.limit);

  const cells = rows.map((row) => ({
    longitude: (row.longitudeIndex + 0.5) * cellDegrees,
    latitude: (row.latitudeIndex + 0.5) * cellDegrees,
    featureCount: row.featureCount,
    voteCount: row.voteCount,
  }));

  const observedAt = rows.reduce<Date | null>((newest, row) => {
    const rowObservedAt = normalizeTimestamp(row.newestCreatedAt);
    if (!rowObservedAt) return newest;
    return !newest || rowObservedAt > newest ? rowObservedAt : newest;
  }, null);

  return { cells, cellDegrees, observedAt };
}

/** Builds the GeoJSON the action-network worker and demand heatmap both validate. */
export function activityGridToFeatureCollection(
  grid: ActivityGrid
): GeoJSON.FeatureCollection<GeoJSON.Point> {
  return {
    type: "FeatureCollection",
    features: grid.cells.map((cell) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [cell.longitude, cell.latitude] },
      properties: {
        voteCount: cell.voteCount,
        featureCount: cell.featureCount,
      },
    })),
  };
}

/** Restricts requests to the ones an account may already read: its own, or its workspaces'. */
function visibleRequestCondition(userId: string, teamId?: string): SQL {
  if (teamId) return eq(strategyRequests.teamId, teamId);
  return or(
    and(eq(strategyRequests.userId, userId), isNull(strategyRequests.teamId)),
    sql`${strategyRequests.teamId} in (select ${teamMembers.teamId} from ${teamMembers} where ${teamMembers.userId} = ${userId})`
  )!;
}

/** Counts requests and votes per strategy type across the requests a caller may read. */
export async function summarizeStrategyActivity(
  database: Database,
  scope: { userId: string; teamId?: string; strategyType?: string }
): Promise<StrategyActivitySummary[]> {
  const conditions: SQL[] = [
    visibleRequestCondition(scope.userId, scope.teamId),
  ];
  if (scope.strategyType) {
    conditions.push(eq(strategyRequests.strategyType, scope.strategyType));
  }

  const rows = await database
    .select({
      strategyType: strategyRequests.strategyType,
      requestCount: sql<number>`count(*)::int`,
      totalVotes: sql<number>`coalesce(sum(${strategyRequests.voteCount}), 0)::int`,
    })
    .from(strategyRequests)
    .where(and(...conditions))
    .groupBy(strategyRequests.strategyType)
    .orderBy(sql`2 desc`, strategyRequests.strategyType);

  return rows.map((row) => ({
    id: row.strategyType,
    strategyType: row.strategyType,
    requestCount: row.requestCount,
    totalVotes: row.totalVotes,
  }));
}
