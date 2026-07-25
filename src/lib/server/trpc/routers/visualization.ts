import { z } from "zod";
import { and, asc, eq, sql } from "drizzle-orm";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { identityFromSession } from "@/lib/server/security/access-control";
import { featureVisibilityCondition } from "@/lib/server/security/layer-access";

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

export const visualizationRouter = router({
  getHeatmapData: publicProcedure
    .input(viewportQuerySchema)
    .query(async ({ ctx, input }) => {
      const rows = await ctx.db
        .select({ properties: features.properties })
        .from(features)
        .innerJoin(layers, eq(features.layerId, layers.id))
        .where(
          and(
            eq(layers.name, input.layerName),
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
      const rows = await ctx.db
        .select({ id: features.id, properties: features.properties })
        .from(features)
        .innerJoin(layers, eq(features.layerId, layers.id))
        .where(
          and(
            eq(layers.name, input.layerName),
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
      const rows = await ctx.db
        .select({ properties: features.properties })
        .from(features)
        .innerJoin(layers, eq(features.layerId, layers.id))
        .where(
          and(
            eq(layers.name, input.layerName),
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
