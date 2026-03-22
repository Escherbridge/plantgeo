import { z } from "zod";
import { router, publicProcedure, contributorProcedure } from "@/lib/server/trpc/init";
import { layers } from "@/lib/server/db/schema";
import { eq, or, isNull } from "drizzle-orm";

const layerCreateSchema = z.object({
  name: z.string().min(1).max(100),
  type: z.string().default("vector"),
  description: z.string().optional(),
  style: z.record(z.unknown()).optional(),
  isPublic: z.boolean().optional(),
  minZoom: z.number().int().min(0).max(22).optional(),
  maxZoom: z.number().int().min(0).max(22).optional(),
  teamId: z.string().uuid().optional(),
  sortOrder: z.number().int().optional(),
});

const layerUpdateSchema = layerCreateSchema.partial().extend({
  id: z.string().uuid(),
});

export const layersRouter = router({
  list: publicProcedure
    .input(z.object({ teamId: z.string().uuid().optional() }).optional())
    .query(async ({ ctx, input }) => {
      if (input?.teamId) {
        return ctx.db
          .select()
          .from(layers)
          .where(or(eq(layers.teamId, input.teamId), isNull(layers.teamId)));
      }
      return ctx.db.select().from(layers).where(isNull(layers.teamId));
    }),

  getById: publicProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const result = await ctx.db
        .select()
        .from(layers)
        .where(eq(layers.id, input.id));
      return result[0] ?? null;
    }),

  create: contributorProcedure
    .input(layerCreateSchema)
    .mutation(async ({ ctx, input }) => {
      const result = await ctx.db.insert(layers).values(input).returning();
      return result[0];
    }),

  update: contributorProcedure
    .input(layerUpdateSchema)
    .mutation(async ({ ctx, input }) => {
      const { id, ...data } = input;
      const result = await ctx.db
        .update(layers)
        .set({ ...data, updatedAt: new Date() })
        .where(eq(layers.id, id))
        .returning();
      return result[0];
    }),

  delete: contributorProcedure
    .input(z.object({ id: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      await ctx.db.delete(layers).where(eq(layers.id, input.id));
      return { success: true };
    }),

  reorder: contributorProcedure
    .input(z.object({ ids: z.array(z.string().uuid()) }))
    .mutation(async ({ ctx, input }) => {
      await Promise.all(
        input.ids.map((id, index) =>
          ctx.db
            .update(layers)
            .set({ sortOrder: index, updatedAt: new Date() })
            .where(eq(layers.id, id))
        )
      );
      return { success: true };
    }),
});
