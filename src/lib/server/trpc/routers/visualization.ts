import { z } from "zod";
import { and, asc, eq, sql } from "drizzle-orm";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { identityFromSession } from "@/lib/server/security/access-control";
import { featureVisibilityCondition } from "@/lib/server/security/layer-access";
import { resolveCachedLayerId } from "@/lib/server/services/environmental-read-model";

const viewportQuerySchema = z
  .object({
    layerName: z.string().trim().min(1).max(100),
    bbox: z.tuple([
      z.number().min(-180).max(180),
      z.number().min(-90).max(90),
      z.number().min(-180).max(180),
      z.number().min(-90).max(90),
    ]),
    limit: z.number().int().min(1).max(2_000).default(1_000),
  })
  .refine(
    ({ bbox: [west, south, east, north] }) =>
      west < east && south < north,
    { message: "bbox must be ordered west,south,east,north", path: ["bbox"] }
  );

function viewportCondition([west, south, east, north]: [
  number,
  number,
  number,
  number,
]) {
  return sql<boolean>`ST_Intersects(
    ${features.geom},
    ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
  )`;
}

/**
 * The three readers below are bounded at 2,000 rows and were unbounded in BYTES: each selected
 * the whole `features.properties` jsonb, which on geo.features carries 1,467 MB of TOAST across
 * the table. Two of them then read four scalars off it and threw the rest away.
 *
 * So each now projects the exact keys it consumes, as a jsonb object built in SQL. That is a
 * projection, not a filter: the same rows come back, carrying only the keys the mapper reads.
 * Detoasting still happens per row -- `->` on a TOASTed jsonb reads the datum -- but the value
 * crossing the wire and living in the Node heap is a handful of scalars instead of a whole
 * upstream payload, which is what a 2,000-row viewport read multiplies.
 *
 * `getPointData` is the exception and keeps the whole blob: its contract RETURNS `properties`
 * to the client, so projecting it would silently change what the caller receives rather than
 * how much the server carries. Its cap stays the row limit.
 */
const HEATMAP_PROPERTY_KEYS = [
  "longitude",
  "lng",
  "latitude",
  "lat",
  "weight",
  "intensity",
  "risk",
] as const;

const FLOW_PROPERTY_KEYS = ["source", "target", "value", "volume"] as const;

/**
 * Builds `jsonb_build_object('k', properties -> 'k', ...)` over exactly the keys a mapper reads.
 *
 * Both binds carry `::text`. `jsonb -> unknown` is ambiguous between `-> text` (object key) and
 * `-> integer` (array element), and postgres-js sends a bare string with no type OID — the same
 * bind-type trap documented on `environmental-read-model.ts`.
 */
function projectedProperties(keys: readonly string[]) {
  return sql<Record<string, unknown>>`jsonb_build_object(${sql.join(
    keys.map((key) => sql`${key}::text, ${features.properties} -> ${key}::text`),
    sql`, `
  )})`;
}

function finiteNumber(value: unknown): number | null {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function coordinatePair(value: Record<string, unknown>): [number, number] | null {
  const longitude = finiteNumber(value.longitude ?? value.lng);
  const latitude = finiteNumber(value.latitude ?? value.lat);
  if (
    longitude === null ||
    longitude < -180 ||
    longitude > 180 ||
    latitude === null ||
    latitude < -90 ||
    latitude > 90
  ) {
    return null;
  }
  return [longitude, latitude];
}

/**
 * Every reader below resolves its client-supplied `layerName` through the one shared resolver, so
 * it filters `features.layer_id` by a constant instead of joining `geo.layers` on `name` for every
 * viewport request.
 *
 * Shared rather than a second local cache on purpose: that one memoized a MISS for the process
 * lifetime, so a layer created at runtime (RUNBOOK.md §0.4) after one early lookup rendered empty
 * forever. The shared resolver never caches a miss, expires its hits, and is bounded for exactly
 * the reason this router needed a bound -- these three procedures are public and take the name
 * from the client.
 */
export const visualizationRouter = router({
  getHeatmapData: publicProcedure
    .input(viewportQuerySchema)
    .query(async ({ ctx, input }) => {
      const layerId = await resolveCachedLayerId(input.layerName);
      if (layerId === null) return [];

      const rows = await ctx.db
        .select({ properties: projectedProperties(HEATMAP_PROPERTY_KEYS) })
        .from(features)
        // Joined on the resolved constant, not `name`, so the layer's own row is
        // still in scope for `featureVisibilityCondition` without blocking pruning.
        .innerJoin(layers, eq(layers.id, layerId))
        .where(
          and(
            eq(features.layerId, layerId),
            featureVisibilityCondition(identityFromSession(ctx.session)),
            viewportCondition(input.bbox)
          )
        )
        .orderBy(asc(features.id))
        .limit(input.limit);

      return rows.flatMap((r) => {
        const p = r.properties as Record<string, unknown>;
        const coordinates = coordinatePair(p);
        return coordinates ? [{
          coordinates,
          weight: finiteNumber(p.weight ?? p.intensity ?? p.risk) ?? 1,
        }] : [];
      });
    }),

  getPointData: publicProcedure
    .input(viewportQuerySchema)
    .query(async ({ ctx, input }) => {
      const layerId = await resolveCachedLayerId(input.layerName);
      if (layerId === null) return [];

      const rows = await ctx.db
        .select({ id: features.id, properties: features.properties })
        .from(features)
        .innerJoin(layers, eq(layers.id, layerId))
        .where(
          and(
            eq(features.layerId, layerId),
            featureVisibilityCondition(identityFromSession(ctx.session)),
            viewportCondition(input.bbox)
          )
        )
        .orderBy(asc(features.id))
        .limit(input.limit);

      return rows.flatMap((r) => {
        const p = r.properties as Record<string, unknown>;
        const coordinates = coordinatePair(p);
        return coordinates ? [{
          coordinates,
          size: finiteNumber(p.size ?? p.value) ?? 1,
          category: String(p.category ?? p.type ?? "default"),
          properties: p,
        }] : [];
      });
    }),

  getFlowData: publicProcedure
    .input(viewportQuerySchema)
    .query(async ({ ctx, input }) => {
      const layerId = await resolveCachedLayerId(input.layerName);
      if (layerId === null) return [];

      const rows = await ctx.db
        .select({ properties: projectedProperties(FLOW_PROPERTY_KEYS) })
        .from(features)
        .innerJoin(layers, eq(layers.id, layerId))
        .where(
          and(
            eq(features.layerId, layerId),
            featureVisibilityCondition(identityFromSession(ctx.session)),
            viewportCondition(input.bbox)
          )
        )
        .orderBy(asc(features.id))
        .limit(input.limit);

      return rows.flatMap((r) => {
        const p = r.properties as Record<string, unknown>;
        const src = p.source as Record<string, unknown> | undefined;
        const tgt = p.target as Record<string, unknown> | undefined;
        if (!src || !tgt) return [];
        const source = coordinatePair(src);
        const target = coordinatePair(tgt);
        return source && target ? [{
          source,
          target,
          value: finiteNumber(p.value ?? p.volume) ?? 1,
        }] : [];
      });
    }),
});
