import { z } from "zod";
import { router, contributorProcedure, expertProcedure } from "@/lib/server/trpc/init";
import { features } from "@/lib/server/db/schema";
import { eq, and } from "drizzle-orm";

export const contributionsRouter = router({
  submitObservation: contributorProcedure
    .input(
      z.object({
        layerId: z.string().uuid(),
        properties: z.record(z.unknown()).default({}),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const [feature] = await ctx.db
        .insert(features)
        .values({
          layerId: input.layerId,
          properties: input.properties,
          status: "pending_review",
        })
        .returning();
      return feature;
    }),

  reviewContribution: expertProcedure
    .input(z.object({ featureId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const [feature] = await ctx.db
        .select()
        .from(features)
        .where(eq(features.id, input.featureId))
        .limit(1);
      return feature ?? null;
    }),

  publishContribution: expertProcedure
    .input(z.object({ featureId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const [updated] = await ctx.db
        .update(features)
        .set({ status: "published", reviewNote: null, updatedAt: new Date() })
        .where(eq(features.id, input.featureId))
        .returning();
      return updated;
    }),

  rejectContribution: expertProcedure
    .input(
      z.object({
        featureId: z.string().uuid(),
        reviewNote: z.string().optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const [updated] = await ctx.db
        .update(features)
        .set({
          status: "rejected",
          reviewNote: input.reviewNote ?? null,
          updatedAt: new Date(),
        })
        .where(eq(features.id, input.featureId))
        .returning();
      return updated;
    }),

  listPendingReview: expertProcedure.query(async ({ ctx }) => {
    return ctx.db
      .select()
      .from(features)
      .where(eq(features.status, "pending_review"));
  }),
});
