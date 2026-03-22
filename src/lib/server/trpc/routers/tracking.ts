import { z } from "zod";
import { router, publicProcedure, contributorProcedure } from "@/lib/server/trpc/init";
import { assets, geofences, alerts, positions } from "@/lib/server/db/schema";
import { eq, desc, and, between } from "drizzle-orm";
import { getRouteHistory } from "@/lib/server/services/tracking";

const assetCreateSchema = z.object({
  name: z.string().min(1).max(100),
  type: z.string().default("vehicle"),
  status: z.enum(["active", "idle", "offline"]).default("offline"),
  metadata: z.record(z.unknown()).optional(),
});

const assetUpdateSchema = assetCreateSchema.partial().extend({
  id: z.string().uuid(),
});

const geofenceCreateSchema = z.object({
  name: z.string().min(1).max(100),
  geometry: z.object({
    type: z.literal("Polygon"),
    coordinates: z.array(z.array(z.tuple([z.number(), z.number()]))),
  }),
  alertOnEnter: z.boolean().default(true),
  alertOnExit: z.boolean().default(true),
});

export const trackingRouter = router({
  listAssets: publicProcedure.query(async ({ ctx }) => {
    return ctx.db.select().from(assets).orderBy(assets.name);
  }),

  getAsset: publicProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const result = await ctx.db
        .select()
        .from(assets)
        .where(eq(assets.id, input.id));
      return result[0] ?? null;
    }),

  createAsset: contributorProcedure
    .input(assetCreateSchema)
    .mutation(async ({ ctx, input }) => {
      const result = await ctx.db.insert(assets).values(input).returning();
      return result[0];
    }),

  updateAsset: contributorProcedure
    .input(assetUpdateSchema)
    .mutation(async ({ ctx, input }) => {
      const { id, ...data } = input;
      const result = await ctx.db
        .update(assets)
        .set(data)
        .where(eq(assets.id, id))
        .returning();
      return result[0];
    }),

  getRouteHistory: publicProcedure
    .input(
      z.object({
        assetId: z.string().uuid(),
        from: z.string().datetime(),
        to: z.string().datetime(),
      })
    )
    .query(async ({ ctx, input }) => {
      return getRouteHistory(
        ctx.db as unknown as import("drizzle-orm/postgres-js").PostgresJsDatabase,
        input.assetId,
        new Date(input.from),
        new Date(input.to)
      );
    }),

  listGeofences: publicProcedure.query(async ({ ctx }) => {
    return ctx.db.select().from(geofences).orderBy(geofences.name);
  }),

  createGeofence: contributorProcedure
    .input(geofenceCreateSchema)
    .mutation(async ({ ctx, input }) => {
      const result = await ctx.db
        .insert(geofences)
        .values({
          name: input.name,
          geometry: input.geometry,
          alertOnEnter: input.alertOnEnter,
          alertOnExit: input.alertOnExit,
        })
        .returning();
      return result[0];
    }),

  deleteGeofence: contributorProcedure
    .input(z.object({ id: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      await ctx.db.delete(geofences).where(eq(geofences.id, input.id));
      return { success: true };
    }),

  getAlerts: publicProcedure.query(async ({ ctx }) => {
    return ctx.db
      .select()
      .from(alerts)
      .orderBy(desc(alerts.createdAt))
      .limit(100);
  }),

  acknowledgeAlert: contributorProcedure
    .input(z.object({ id: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const result = await ctx.db
        .update(alerts)
        .set({ acknowledged: true })
        .where(eq(alerts.id, input.id))
        .returning();
      return result[0];
    }),
});
