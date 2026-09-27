import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { router, contributorProcedure, expertProcedure } from "@/lib/server/trpc/init";
import { features, layers } from "@/lib/server/db/schema";
import { and, eq, inArray, isNull } from "drizzle-orm";
import { isDataInterventionType } from "@/lib/environmental/data-intervention";

const APPLICATION_CONTRIBUTION_LAYERS = ["interventions"];
/**
 * Properties this generic endpoint may never accept from the client: origin
 * and authorship claims (stamped server-side instead), plus every field that
 * would let a bare `z.record(z.unknown())` row impersonate a validated
 * `interventions.submitIntervention` / `submitRequest` submission on the same
 * table and layer -- `geometry` (this path enforces no vertex ceiling or area
 * cap), `category`/`kind` (misclassifies the row for the readers that key off
 * them), `publicationConsent` (would expose the row through
 * `interventions.listProposed` / `getInterventionDetail` before any expert
 * reviews it), `status` (the moderation state some readers still project
 * from `properties`, distinct from the `features.status` column this
 * endpoint always sets to `pending_review`) and `id` (the machine-ingress
 * upsert key in `intervention-store.ts`; a client-chosen value can capture a
 * partner feature's future upsert as an UPDATE instead of an INSERT).
 */
const RESERVED_CONTRIBUTION_PROPERTIES = [
  "dataOrigin", "provenance", "source", "sourceType", "dataDetails",
  "submittedByUserId", "submittedByTeamId",
  "geometry", "category", "kind", "publicationConsent", "id", "status",
];

/** Canonical pending-only review errors; see src/lib/server/AGENTS.md. */
const contributionNotFound = () =>
  new TRPCError({ code: "NOT_FOUND", message: "Contribution not found" });
const contributionNotPending = () =>
  new TRPCError({ code: "CONFLICT", message: "Contribution is no longer awaiting review" });

export const contributionsRouter = router({
  submitObservation: contributorProcedure
    .input(
      z.object({
        layerId: z.string().uuid(),
        properties: z.record(z.unknown()).default({}).superRefine((properties, context) => {
          if (RESERVED_CONTRIBUTION_PROPERTIES.some((key) => key in properties)) {
            context.addIssue({ code: z.ZodIssueCode.custom, message: "This property is assigned by the server or reserved for the validated submission forms" });
          }
          if (isDataInterventionType(properties.type) || properties.category === "data") {
            context.addIssue({ code: z.ZodIssueCode.custom, message: "Data interventions must use the validated data submission form" });
          }
        }),
      }).strict()
    )
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      if (!userId) throw new TRPCError({ code: "UNAUTHORIZED", message: "User ID required" });
      const [layer] = await ctx.db
        .select({ id: layers.id })
        .from(layers)
        .where(and(
          eq(layers.id, input.layerId),
          inArray(layers.name, APPLICATION_CONTRIBUTION_LAYERS),
          isNull(layers.teamId),
        ))
        .limit(1);
      if (!layer) {
        throw new TRPCError({ code: "BAD_REQUEST", message: "Observations may only be submitted to a supported community layer" });
      }
      const [feature] = await ctx.db
        .insert(features)
        .values({
          layerId: input.layerId,
          properties: { ...input.properties, submittedByUserId: userId, dataOrigin: "community" },
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
      if (!feature) throw contributionNotFound();

      const [updated] = await ctx.db
        .update(features)
        .set({ status: "published", reviewNote: null, updatedAt: new Date() })
        .where(
          and(
            eq(features.id, input.featureId),
            eq(features.layerId, feature.layerId),
            eq(features.status, "pending_review")
          )
        )
        .returning();
      if (!updated) throw contributionNotPending();
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
      if (!feature) throw contributionNotFound();

      const [updated] = await ctx.db
        .update(features)
        .set({
          status: "rejected",
          reviewNote: input.reviewNote ?? null,
          updatedAt: new Date(),
        })
        .where(
          and(
            eq(features.id, input.featureId),
            eq(features.layerId, feature.layerId),
            eq(features.status, "pending_review")
          )
        )
        .returning();
      if (!updated) throw contributionNotPending();
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
