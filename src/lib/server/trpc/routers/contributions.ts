import { z } from "zod";
import { createHash } from "node:crypto";
import { TRPCError } from "@trpc/server";
import { router, contributorProcedure, expertProcedure, type Context } from "@/lib/server/trpc/init";
import { features, layers, users } from "@/lib/server/db/schema";
import { and, eq, inArray, sql } from "drizzle-orm";
import { MAX_INGRESS_BODY_BYTES } from "@/lib/server/security/ingress";
import { assertValidInterventionGeometry } from "@/lib/server/services/intervention-geometry-validation";
import {
  countInterventionGeometryPositions,
  InterventionGeometrySchema,
  MAX_INTERVENTION_GEOMETRY_POSITIONS,
} from "@/lib/server/services/intervention-geometry";

const reviewNoteSchema = z.string().trim().min(1).max(2_000);
const reviewDecisionSchema = z.object({
  featureId: z.string().uuid(),
  expectedReviewVersion: z.string().regex(/^[a-f0-9]{64}$/),
});
const reviewProjection = {
  id: features.id,
  layerId: features.layerId,
  layerName: layers.name,
  properties: features.properties,
  status: features.status,
  reviewNote: features.reviewNote,
  createdAt: features.createdAt,
  updatedAt: features.updatedAt,
  versionTimestamp: sql<string | null>`${features.updatedAt}::text`,
  authorExists: sql<boolean>`exists(select 1 from ${users} where ${users.id}::text = ${features.properties} ->> 'submittedByUserId')`,
};

/** Bind each decision to the submission the moderator actually inspected. */
function withReviewVersion<T extends {
  id: string; layerId: string; status: string | null; properties: unknown;
  reviewNote: string | null; updatedAt: Date | null; versionTimestamp: string | null;
}>(feature: T) {
  const reviewVersion = createHash("sha256").update(JSON.stringify([
    feature.id, feature.layerId, feature.status, feature.properties,
    feature.reviewNote, feature.versionTimestamp ?? feature.updatedAt?.toISOString() ?? null,
  ])).digest("hex");
  return { ...feature, reviewVersion };
}

function assertDisplayedReviewVersion(actual: string, expected: string) {
  if (actual !== expected) throw new TRPCError({ code: "CONFLICT", message: "This submission changed since you opened it. Review the refreshed boundary before deciding." });
}

function isPublishableIntervention(value: unknown): boolean {
  const properties = value as Record<string, unknown> | null;
  if (properties?.publicationConsent !== true || typeof properties.submittedByUserId !== "string" || !properties.submittedByUserId.trim()) return false;
  if (Buffer.byteLength(JSON.stringify(properties), "utf8") > MAX_INGRESS_BODY_BYTES) return false;
  const geometry = InterventionGeometrySchema.safeParse(properties.geometry);
  return geometry.success && countInterventionGeometryPositions(geometry.data) <= MAX_INTERVENTION_GEOMETRY_POSITIONS;
}

async function readContribution(ctx: Context, featureId: string) {
  const [feature] = await ctx.db.select(reviewProjection).from(features)
    .innerJoin(layers, eq(layers.id, features.layerId))
    .where(eq(features.id, featureId)).limit(1);
  if (!feature) throw new TRPCError({ code: "NOT_FOUND", message: "Contribution not found" });
  return withReviewVersion(feature);
}

/** A concurrent revision or review must be read again before applying a decision. */
function reviewedVersion(feature: Awaited<ReturnType<typeof readContribution>>) {
  return and(eq(features.id, feature.id), eq(features.layerId, feature.layerId),
    eq(features.status, feature.status ?? ""),
    sql`${features.properties} = ${JSON.stringify(feature.properties)}::jsonb`,
    sql`${features.updatedAt} is not distinct from ${feature.versionTimestamp}::timestamptz`);
}

function assertPending(status: string | null) {
  if (status !== "pending_review") throw new TRPCError({ code: "PRECONDITION_FAILED", message: "This contribution is no longer awaiting review. Refresh the queue." });
}

function assertUpdated<T>(updated: T | undefined): T {
  if (!updated) throw new TRPCError({ code: "CONFLICT", message: "This contribution changed during review. Refresh it before deciding." });
  return updated;
}

export const contributionsRouter = router({
  submitObservation: contributorProcedure
    .input(z.object({ layerId: z.string().uuid(), properties: z.record(z.unknown()).default({}) }))
    .mutation(async ({ ctx, input }) => {
      const [layer] = await ctx.db.select({ name: layers.name }).from(layers).where(eq(layers.id, input.layerId)).limit(1);
      if (!layer) throw new TRPCError({ code: "NOT_FOUND", message: "Layer not found" });
      if (layer.name === "interventions") throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Use the intervention submission workflow to record geometry, authorship and consent." });
      const [feature] = await ctx.db.insert(features).values({ layerId: input.layerId, properties: input.properties, status: "pending_review" }).returning();
      return feature;
    }),

  reviewContribution: expertProcedure.input(z.object({ featureId: z.string().uuid() }))
    .query(async ({ ctx, input }) => readContribution(ctx, input.featureId)),

  /** The only interactive transition to the publication status read by public tiles. */
  publishContribution: expertProcedure
    .input(reviewDecisionSchema.extend({ recoveryReviewNote: reviewNoteSchema.optional() }))
    .mutation(async ({ ctx, input }) => {
      const feature = await readContribution(ctx, input.featureId);
      assertDisplayedReviewVersion(feature.reviewVersion, input.expectedReviewVersion);
      const recovery = feature.status === "approved";
      if (recovery) {
        if (feature.layerName !== "interventions" || !input.recoveryReviewNote || !feature.authorExists) {
          throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Legacy approval requires an existing contributor identity and an individual recovery review note." });
        }
      } else {
        assertPending(feature.status);
      }
      if (feature.layerName === "interventions" && !isPublishableIntervention(feature.properties)) {
        throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Publication requires valid intervention geometry, recorded consent and a known contributor. Request a corrected submission." });
      }
      if (feature.layerName === "interventions") {
        const properties = feature.properties as Record<string, unknown>;
        await assertValidInterventionGeometry(ctx.db, InterventionGeometrySchema.parse(properties.geometry));
      }
      const [updated] = await ctx.db.update(features).set({
        status: "published",
        reviewNote: recovery ? input.recoveryReviewNote : null,
        updatedAt: new Date(),
      }).where(reviewedVersion(feature)).returning();
      return assertUpdated(updated);
    }),

  rejectContribution: expertProcedure
    .input(reviewDecisionSchema.extend({ reviewNote: reviewNoteSchema }))
    .mutation(async ({ ctx, input }) => {
      const feature = await readContribution(ctx, input.featureId);
      assertDisplayedReviewVersion(feature.reviewVersion, input.expectedReviewVersion);
      assertPending(feature.status);
      const [updated] = await ctx.db.update(features).set({ status: "rejected", reviewNote: input.reviewNote, updatedAt: new Date() })
        .where(reviewedVersion(feature)).returning();
      return assertUpdated(updated);
    }),

  requestRevisionContribution: expertProcedure
    .input(reviewDecisionSchema.extend({ reviewNote: reviewNoteSchema }))
    .mutation(async ({ ctx, input }) => {
      const feature = await readContribution(ctx, input.featureId);
      assertDisplayedReviewVersion(feature.reviewVersion, input.expectedReviewVersion);
      if (feature.layerName !== "interventions") throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Revision requests are supported only for intervention submissions." });
      assertPending(feature.status);
      const [updated] = await ctx.db.update(features).set({ status: "revision_requested", reviewNote: input.reviewNote, updatedAt: new Date() })
        .where(reviewedVersion(feature)).returning();
      return assertUpdated(updated);
    }),

  /** Pending submissions plus individually recoverable legacy intervention approvals. */
  listPendingReview: expertProcedure.query(async ({ ctx }) => {
    const [interventionsLayer] = await ctx.db.select({ id: layers.id }).from(layers)
      .where(eq(layers.name, "interventions")).limit(1);
    if (!interventionsLayer) throw new TRPCError({ code: "PRECONDITION_FAILED", message: "The interventions layer is not provisioned in this environment." });
    const rows = await ctx.db.select(reviewProjection).from(features)
      .innerJoin(layers, eq(layers.id, features.layerId))
      .where(inArray(features.status, ["pending_review", "approved"]))
      .orderBy(features.createdAt);
    return rows.filter((row) => row.status === "pending_review" ||
      (row.layerName === "interventions" && row.authorExists && isPublishableIntervention(row.properties)))
      .map(withReviewVersion);
  }),
});
