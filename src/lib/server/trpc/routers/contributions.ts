import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { router, contributorProcedure, expertProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { and, eq } from "drizzle-orm";

/**
 * The error a review mutation raises when its UPDATE matched no row.
 *
 * Deliberately not raised by the pre-flight SELECT, which keeps returning `undefined` as this
 * router always has for a feature that is simply gone. Passing the pre-flight and still matching
 * nothing is a delete racing the mutation, and a review decision that applied to no row must not
 * resolve as success.
 */
const contributionNotFound = () =>
  new TRPCError({ code: "NOT_FOUND", message: "Contribution not found" });

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
      // Resolved first so the update below can pin `layer_id` and touch one
      // partition instead of probing every partition for a bare `id` match.
      const [feature] = await ctx.db
        .select({ layerId: features.layerId })
        .from(features)
        .where(eq(features.id, input.featureId))
        .limit(1);
      if (!feature) return undefined;

      const [updated] = await ctx.db
        .update(features)
        .set({ status: "published", reviewNote: null, updatedAt: new Date() })
        .where(
          and(eq(features.id, input.featureId), eq(features.layerId, feature.layerId))
        )
        .returning();
      if (!updated) throw contributionNotFound();
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
      const [feature] = await ctx.db
        .select({ layerId: features.layerId })
        .from(features)
        .where(eq(features.id, input.featureId))
        .limit(1);
      if (!feature) return undefined;

      const [updated] = await ctx.db
        .update(features)
        .set({
          status: "rejected",
          reviewNote: input.reviewNote ?? null,
          updatedAt: new Date(),
        })
        .where(
          and(eq(features.id, input.featureId), eq(features.layerId, feature.layerId))
        )
        .returning();
      if (!updated) throw contributionNotFound();
      return updated;
    }),

  /**
   * The review queue.
   *
   * Projected rather than `select()`-ed whole, and joined to the layer, for one reason: a
   * reviewer has to be able to tell what they are approving. The bare select surfaced two
   * UUIDs and a PostGIS `geom` blob no surface reads, while everything that identifies a
   * submission -- name, type, description and the GeoJSON geometry that
   * `interventions.submitIntervention` writes -- sat unread inside `properties`. Read-only
   * widening: same gate, same rows, no column or status change.
   *
   * The join is inner because `features.layer_id` is NOT NULL with a foreign key to
   * `geo.layers`, so it can drop nothing. The query stays layer-agnostic -- it filters on
   * status alone -- which is why the layer name is carried per row rather than assumed.
   */
  listPendingReview: expertProcedure.query(async ({ ctx }) => {
    return ctx.db
      .select({
        id: features.id,
        layerId: features.layerId,
        layerName: layers.name,
        properties: features.properties,
        status: features.status,
        createdAt: features.createdAt,
      })
      .from(features)
      .innerJoin(layers, eq(layers.id, features.layerId))
      .where(eq(features.status, "pending_review"))
      // Oldest first: a queue nobody has reached is the thing a reviewer should see.
      .orderBy(features.createdAt);
  }),
});
