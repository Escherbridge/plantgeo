import { z } from "zod";
import { eq } from "drizzle-orm";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";

export const visualizationRouter = router({
  getHeatmapData: publicProcedure
    .input(z.object({ layerName: z.string() }))
    .query(async ({ ctx, input }) => {
      const layerRows = await ctx.db
        .select({ id: layers.id })
        .from(layers)
        .where(eq(layers.name, input.layerName));

      if (!layerRows[0]) return [];

      const rows = await ctx.db
        .select({ properties: features.properties })
        .from(features)
        .where(eq(features.layerId, layerRows[0].id));

      return rows.map((r) => {
        const p = r.properties as Record<string, unknown>;
        return {
          coordinates: [
            Number(p.longitude ?? p.lng ?? 0),
            Number(p.latitude ?? p.lat ?? 0),
          ] as [number, number],
          weight: Number(p.weight ?? p.intensity ?? p.risk ?? 1),
        };
      });
    }),

  getPointData: publicProcedure
    .input(z.object({ layerName: z.string() }))
    .query(async ({ ctx, input }) => {
      const layerRows = await ctx.db
        .select({ id: layers.id })
        .from(layers)
        .where(eq(layers.name, input.layerName));

      if (!layerRows[0]) return [];

      const rows = await ctx.db
        .select({ id: features.id, properties: features.properties })
        .from(features)
        .where(eq(features.layerId, layerRows[0].id));

      return rows.map((r) => {
        const p = r.properties as Record<string, unknown>;
        return {
          coordinates: [
            Number(p.longitude ?? p.lng ?? 0),
            Number(p.latitude ?? p.lat ?? 0),
          ] as [number, number],
          size: Number(p.size ?? p.value ?? 1),
          category: String(p.category ?? p.type ?? "default"),
          properties: p,
        };
      });
    }),

  getFlowData: publicProcedure
    .input(z.object({ layerName: z.string() }))
    .query(async ({ ctx, input }) => {
      const layerRows = await ctx.db
        .select({ id: layers.id })
        .from(layers)
        .where(eq(layers.name, input.layerName));

      if (!layerRows[0]) return [];

      const rows = await ctx.db
        .select({ properties: features.properties })
        .from(features)
        .where(eq(features.layerId, layerRows[0].id));

      return rows.map((r) => {
        const p = r.properties as Record<string, unknown>;
        const src = p.source as Record<string, unknown> | undefined;
        const tgt = p.target as Record<string, unknown> | undefined;
        return {
          source: [
            Number(src?.longitude ?? src?.lng ?? 0),
            Number(src?.latitude ?? src?.lat ?? 0),
          ] as [number, number],
          target: [
            Number(tgt?.longitude ?? tgt?.lng ?? 0),
            Number(tgt?.latitude ?? tgt?.lat ?? 0),
          ] as [number, number],
          value: Number(p.value ?? p.volume ?? 1),
        };
      });
    }),
});
