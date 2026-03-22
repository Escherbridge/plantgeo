import { z } from "zod";
import { router, protectedProcedure } from "@/lib/server/trpc/init";
import {
  environmentalAlerts,
  watchedLocations,
  alertSubscriptions,
} from "@/lib/server/db/schema";
import { eq, and, desc, sql, count } from "drizzle-orm";

export const alertsRouter = router({
  /**
   * Get alerts for the authenticated user, sorted by severity + recency.
   */
  getAlerts: protectedProcedure
    .input(
      z.object({
        limit: z.number().int().min(1).max(100).default(20),
        unreadOnly: z.boolean().default(false),
      })
    )
    .query(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      if (!userId) return [];

      const conditions = [eq(environmentalAlerts.userId, userId)];
      if (input.unreadOnly) {
        conditions.push(eq(environmentalAlerts.isRead, false));
      }

      return ctx.db
        .select()
        .from(environmentalAlerts)
        .where(and(...conditions))
        .orderBy(
          // Sort critical first, then warning, then info; within each severity, newest first
          sql`CASE ${environmentalAlerts.severity} WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END`,
          desc(environmentalAlerts.createdAt)
        )
        .limit(input.limit);
    }),

  /**
   * Count unread alerts for the badge.
   */
  getUnreadCount: protectedProcedure.query(async ({ ctx }) => {
    const userId = (ctx.session.user as { id?: string } | undefined)?.id;
    if (!userId) return 0;

    const [result] = await ctx.db
      .select({ count: count() })
      .from(environmentalAlerts)
      .where(
        and(
          eq(environmentalAlerts.userId, userId),
          eq(environmentalAlerts.isRead, false)
        )
      );

    return result?.count ?? 0;
  }),

  /**
   * Mark a single alert as read.
   */
  markRead: protectedProcedure
    .input(z.object({ alertId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      if (!userId) return;

      await ctx.db
        .update(environmentalAlerts)
        .set({ isRead: true })
        .where(
          and(
            eq(environmentalAlerts.id, input.alertId),
            eq(environmentalAlerts.userId, userId)
          )
        );
    }),

  /**
   * Mark all alerts as read for the user.
   */
  markAllRead: protectedProcedure.mutation(async ({ ctx }) => {
    const userId = (ctx.session.user as { id?: string } | undefined)?.id;
    if (!userId) return;

    await ctx.db
      .update(environmentalAlerts)
      .set({ isRead: true })
      .where(
        and(
          eq(environmentalAlerts.userId, userId),
          eq(environmentalAlerts.isRead, false)
        )
      );
  }),

  /**
   * Add a new watched location.
   */
  addWatchedLocation: protectedProcedure
    .input(
      z.object({
        name: z.string().min(1).max(100),
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
        radiusKm: z.number().int().min(1).max(500).default(50),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      if (!userId) throw new Error("User ID required");

      const [inserted] = await ctx.db
        .insert(watchedLocations)
        .values({
          userId,
          name: input.name,
          lat: input.lat,
          lon: input.lon,
          radiusKm: input.radiusKm,
        })
        .returning();

      return inserted;
    }),

  /**
   * List watched locations for the authenticated user.
   */
  getWatchedLocations: protectedProcedure.query(async ({ ctx }) => {
    const userId = (ctx.session.user as { id?: string } | undefined)?.id;
    if (!userId) return [];

    return ctx.db
      .select()
      .from(watchedLocations)
      .where(eq(watchedLocations.userId, userId))
      .orderBy(desc(watchedLocations.createdAt));
  }),

  /**
   * Upsert an alert subscription for a watched location.
   */
  updateSubscription: protectedProcedure
    .input(
      z.object({
        locationId: z.string().uuid(),
        alertType: z.enum([
          "fire_proximity",
          "drought_escalation",
          "streamflow_critical",
          "priority_zone_created",
        ]),
        emailEnabled: z.boolean(),
        inAppEnabled: z.boolean(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      if (!userId) throw new Error("User ID required");

      // Verify the location belongs to the user
      const [location] = await ctx.db
        .select({ id: watchedLocations.id })
        .from(watchedLocations)
        .where(
          and(
            eq(watchedLocations.id, input.locationId),
            eq(watchedLocations.userId, userId)
          )
        )
        .limit(1);

      if (!location) throw new Error("Watched location not found");

      // Upsert: check for existing subscription
      const [existing] = await ctx.db
        .select({ id: alertSubscriptions.id })
        .from(alertSubscriptions)
        .where(
          and(
            eq(alertSubscriptions.userId, userId),
            eq(alertSubscriptions.watchedLocationId, input.locationId),
            eq(alertSubscriptions.alertType, input.alertType)
          )
        )
        .limit(1);

      if (existing) {
        const [updated] = await ctx.db
          .update(alertSubscriptions)
          .set({
            emailEnabled: input.emailEnabled,
            inAppEnabled: input.inAppEnabled,
          })
          .where(eq(alertSubscriptions.id, existing.id))
          .returning();
        return updated;
      }

      const [inserted] = await ctx.db
        .insert(alertSubscriptions)
        .values({
          userId,
          watchedLocationId: input.locationId,
          alertType: input.alertType,
          emailEnabled: input.emailEnabled,
          inAppEnabled: input.inAppEnabled,
        })
        .returning();

      return inserted;
    }),
});
