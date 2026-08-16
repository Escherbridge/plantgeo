import { count, eq, sql } from "drizzle-orm";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";
import { layers } from "@/lib/server/db/schema";

type Db = PostgresJsDatabase<typeof schema>;

/**
 * Every reader here is a publicProcedure, and every one of them used to aggregate all 4.97M
 * published rows of `geo.features` per call. They now read two pre-aggregated relations:
 *
 *   - `geo.mv_layer_feature_stats`   -- one row per layer (11 today)
 *   - `geo.mv_layer_hourly_activity` -- one row per (layer, hour) over the trailing 168 hours
 *
 * `geo.layers` is joined at read time rather than denormalized into the matviews, and that
 * join is what enforces `is_public`. Trusting a matview to have applied the visibility rule
 * would put the rule in two places, and the cheaper place to get it wrong is the one nobody
 * reads. The layers table is 11 rows and trivially resident, so the join costs nothing.
 *
 * Rationale, sizing and the refresh contract: src/lib/server/AGENTS.md §pre-aggregation.
 */

/** Rows come back as text from db.execute; every count is normalized here, never at a caller. */
function toCount(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Published feature count per public layer.
 *
 * `published_count > 0` is REQUIRED for parity, not an optimization. The predicate this replaced
 * inner-joined `geo.features` to `geo.layers` and grouped, so a public layer with zero published
 * features produced NO ROW at all. `geo.mv_layer_feature_stats` LEFT JOINs on purpose (an empty
 * layer must be reportable as empty rather than absent), so without this filter every caller --
 * the dashboard tiles, `layerStats` -- would start seeing a zero row for `interventions`, which
 * the DDL names as exactly that case. The matview keeps the richer answer; this reader keeps the
 * contract it had.
 */
export async function getFeatureCountByLayer(db: Db) {
  const rows = await db.execute<{ layer_id: string; published_count: string | number }>(sql`
    SELECT stats.layer_id, stats.published_count
    FROM geo.mv_layer_feature_stats stats
    JOIN geo.layers l ON l.id = stats.layer_id
    WHERE l.is_public IS TRUE
      AND stats.published_count > 0
    ORDER BY stats.layer_id
  `);
  return rows.map((row) => ({
    layerId: row.layer_id,
    count: toCount(row.published_count),
  }));
}

/**
 * The layer list WITH its counts, in one read.
 *
 * `layerStats` used to call `getFeatureCountByLayer` and then `SELECT * FROM layers` -- the
 * second of which pulls every layer's `style` jsonb across the wire to recover one `name`
 * column. `geo.layers` is already joined here for `is_public`, so the name comes off the LIVE
 * row, not off `stats.layer_name`: the matview's copy of the name is only as fresh as its last
 * refresh (up to 6 h), and a renamed layer showing its old name in the panel would be a new
 * staleness this reader never had. Selecting one `name` from an 11-row table already in the join
 * costs nothing.
 *
 * Carries the same `published_count > 0` filter as `getFeatureCountByLayer`, for the same
 * reason -- `layerStats` derived its rows from that function's output, so its row set must not
 * widen either.
 */
export async function getLayerFeatureStats(db: Db) {
  const rows = await db.execute<{
    layer_id: string;
    layer_name: string | null;
    published_count: string | number;
  }>(sql`
    SELECT stats.layer_id, l.name AS layer_name, stats.published_count
    FROM geo.mv_layer_feature_stats stats
    JOIN geo.layers l ON l.id = stats.layer_id
    WHERE l.is_public IS TRUE
      AND stats.published_count > 0
    ORDER BY stats.layer_id
  `);
  return rows.map((row) => ({
    layerId: row.layer_id,
    layerName: row.layer_name ?? "Unknown",
    count: toCount(row.published_count),
  }));
}

/**
 * Features created in the trailing `hours`, per layer.
 *
 * Bounded at 168 hours by the caller's own zod schema, which is exactly the window
 * `geo.mv_layer_hourly_activity` materializes -- so any N the router accepts is a SUM over an
 * indexed range of at most 168 buckets per layer. The predicate this replaces was
 * `created_at >= now() - N hours` with no index that could serve it: `idx_features_layer_created_at`
 * leads with `layer_id`, so a created_at-only filter fell through to a scan of every published
 * public feature.
 *
 * TWO DISCLOSED DEPARTURES FROM THE EXACT ROLLING WINDOW THIS REPLACED, in opposite directions.
 * The old predicate was `created_at >= now() - N hours`, an exact instant. Hour buckets cannot
 * express that, so:
 *
 *   - OVER-COUNT, up to 59 minutes. `hour_bucket >= date_trunc('hour', now()) - N hours` admits
 *     the ENTIRE oldest bucket. At now = 14:30 with hours = 24, features created between 14:00
 *     and 14:30 the previous day are counted and previously were not. Including the whole bucket
 *     is preferred to excluding it: the alternative silently drops up to an hour of the window a
 *     caller asked for, and a "last 24 hours" panel that under-reports is the worse failure.
 *   - UNDER-COUNT, bounded by staleness. An hour bucket the matview has not caught up to yet is
 *     reported as absent rather than estimated. The refresh runs on a 900 s minimum interval / 1 h
 *     maximum staleness with a watermark carrying the current hour, so the newest bucket is the
 *     one that can lag.
 *
 * Both are properties of the bucket grain, not of the rollup being wrong; neither is estimated
 * away, because inventing the remainder would not be a true statement about what was written.
 */
export async function getRecentActivity(db: Db, hours = 24) {
  const rows = await db.execute<{ layer_id: string; feature_count: string | number }>(sql`
    SELECT activity.layer_id, SUM(activity.feature_count) AS feature_count
    FROM geo.mv_layer_hourly_activity activity
    JOIN geo.layers l ON l.id = activity.layer_id
    WHERE l.is_public IS TRUE
      AND activity.hour_bucket >= date_trunc('hour', now()) - (${hours}::integer * interval '1 hour')
    GROUP BY activity.layer_id
    ORDER BY activity.layer_id
  `);
  return rows.map((row) => ({
    layerId: row.layer_id,
    count: toCount(row.feature_count),
  }));
}

export async function getSystemStats(db: Db) {
  const [layerResult, featureResult] = await Promise.all([
    // 11 rows. Left as a real count: there is no cheaper form, and no matview would be.
    db.select({ count: count() }).from(layers).where(eq(layers.isPublic, true)),
    db.execute<{ feature_count: string | number | null }>(sql`
      SELECT COALESCE(SUM(stats.published_count), 0) AS feature_count
      FROM geo.mv_layer_feature_stats stats
      JOIN geo.layers l ON l.id = stats.layer_id
      WHERE l.is_public IS TRUE
    `),
  ]);
  return {
    layerCount: layerResult[0]?.count ?? 0,
    featureCount: toCount(featureResult[0]?.feature_count),
    activeStreams: null,
  };
}
