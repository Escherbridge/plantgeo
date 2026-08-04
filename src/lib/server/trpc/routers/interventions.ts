import { TRPCError } from "@trpc/server";
import { and, desc, eq, sql } from "drizzle-orm";
import { z } from "zod";
import {
  contributorProcedure,
  protectedProcedure,
  router,
  type Context,
} from "@/lib/server/trpc/init";
import { features, layers, teamMembers } from "@/lib/server/db/schema";
import { isTeamEditorRole } from "@/lib/server/security/access-control";
import {
  countInterventionGeometryPositions,
  InterventionGeometrySchema,
  MAX_INTERVENTION_GEOMETRY_POSITIONS,
} from "@/lib/server/services/intervention-geometry";

/**
 * User-authored intervention recommendations.
 *
 * Why this router exists, and why it is separate from `wildfire`:
 * `geo.layers.name = 'interventions'` is a domain-generic "ecosystem
 * intervention sites" bucket, while `wildfire` is themed around fire risk and
 * detections. The retired `wildfire.createIntervention` conflated the two: it
 * wrote a fire-specific `strategyId`/`priority` vocabulary into the same layer
 * that `src/lib/environmental/intervention.ts` and the machine-ingress route at
 * `src/app/api/ingest/interventions/route.ts` describe with `InterventionType`,
 * it never set `status` (so rows took the column default `published` and
 * skipped review entirely), and it accepted unvalidated `z.record` geometry.
 *
 * Three writers are deliberately kept apart:
 *   - this router: interactive, NextAuth-session authored, always enters review;
 *   - `src/app/api/ingest/interventions/route.ts`: INGEST_SECRET bearer-token
 *     machine ingress for a future automated/partner feed, untouched here;
 *   - `contributions.publishContribution` / `rejectContribution`
 *     (expertProcedure): the only transitions out of review.
 *
 * No row is ever authored by this module itself: the layer stays empty until a
 * signed-in contributor submits their own recommendation.
 */

/** The `geo.layers` row every intervention feature belongs to. */
const INTERVENTIONS_LAYER_NAME = "interventions";

/**
 * The status that distinguishes a recommendation from an approved
 * intervention. `geo.intervention_tiles` and `wildfire.getInterventions` both
 * filter on `status = 'published'`, so a recommendation is invisible on the map
 * until an expert publishes it through the existing contributions moderation
 * procedures.
 */
const RECOMMENDATION_STATUS = "pending_review";

const MAX_SUBMISSION_DESCRIPTION_LENGTH = 2_000;

/**
 * The interactive geometry bound. `/api/trpc/[trpc]/route.ts` already caps a
 * request body at `MAX_INGRESS_BODY_BYTES` so Zod never walks an unbounded
 * array, exactly as `parseBoundedJson` does for the machine-ingress route; this
 * is the second, semantic half of that bound - a submitted site that needs more
 * than `MAX_INTERVENTION_GEOMETRY_POSITIONS` vertices is not a hand-drawn
 * recommendation, whatever its byte size.
 */
const BoundedInterventionGeometrySchema = InterventionGeometrySchema.superRefine(
  (geometry, context) => {
    if (
      countInterventionGeometryPositions(geometry) >
      MAX_INTERVENTION_GEOMETRY_POSITIONS
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: `Geometry must not exceed ${MAX_INTERVENTION_GEOMETRY_POSITIONS} coordinate positions`,
      });
    }
  }
);

/** Mirrors `InterventionType` in `src/lib/environmental/intervention.ts`. */
const InterventionTypeSchema = z.enum([
  "reforestation",
  "silvopasture",
  "cover_cropping",
  "biochar",
  "keyline",
]);

export type InterventionSubmissionType = z.infer<typeof InterventionTypeSchema>;

const submissionProjection = {
  id: features.id,
  properties: features.properties,
  status: features.status,
  reviewNote: features.reviewNote,
  createdAt: features.createdAt,
  updatedAt: features.updatedAt,
};

function currentUserId(session: { user?: unknown }): string {
  const userId = (session.user as { id?: string } | undefined)?.id;
  if (!userId) {
    throw new TRPCError({ code: "UNAUTHORIZED", message: "User ID required" });
  }
  return userId;
}

/** Re-reads workspace membership from the database inside the acting request. */
async function requireTeamAccess(
  ctx: Context,
  teamId: string,
  userId: string,
  requireEditor: boolean
): Promise<void> {
  const [membership] = await ctx.db
    .select({ teamRole: teamMembers.teamRole })
    .from(teamMembers)
    .where(and(eq(teamMembers.teamId, teamId), eq(teamMembers.userId, userId)))
    .limit(1);

  if (!membership || (requireEditor && !isTeamEditorRole(membership.teamRole))) {
    throw new TRPCError({
      code: "NOT_FOUND",
      message: "Partner workspace not found",
    });
  }
}

/** Resolves the provisioned interventions layer; never creates one. */
async function resolveInterventionsLayerId(ctx: Context): Promise<string> {
  const [layer] = await ctx.db
    .select({ id: layers.id })
    .from(layers)
    .where(eq(layers.name, INTERVENTIONS_LAYER_NAME))
    .limit(1);

  if (!layer) {
    throw new TRPCError({
      code: "PRECONDITION_FAILED",
      message:
        "The interventions layer is not provisioned in this environment; submissions are closed until it is migrated in",
    });
  }
  return layer.id;
}

export const interventionsRouter = router({
  /**
   * Record one signed-in contributor's intervention recommendation as a feature
   * held for expert review.
   */
  submitIntervention: contributorProcedure
    .input(
      z.object({
        name: z.string().trim().min(3).max(256),
        type: InterventionTypeSchema,
        description: z
          .string()
          .trim()
          .max(MAX_SUBMISSION_DESCRIPTION_LENGTH)
          .optional(),
        // Reuses the validator the machine-ingress route already relies on,
        // under an interactive vertex ceiling.
        geometry: BoundedInterventionGeometrySchema,
        teamId: z.string().uuid().optional(),
        publicationConsent: z.literal(true),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      if (input.teamId) {
        await requireTeamAccess(ctx, input.teamId, userId, true);
      }
      const layerId = await resolveInterventionsLayerId(ctx);

      // `geo.features.geom` is populated by the `geo_features_sync_geom`
      // BEFORE trigger from `properties.geometry`; `geometry_id` stays NULL
      // because a one-off user submission never enters the Type-2 conformed
      // geometry dimension, which only the warehouse backfill governs.
      const [submitted] = await ctx.db
        .insert(features)
        .values({
          layerId,
          status: RECOMMENDATION_STATUS,
          properties: {
            name: input.name,
            type: input.type,
            description: input.description ?? null,
            geometry: input.geometry,
            submittedByUserId: userId,
            submittedByTeamId: input.teamId ?? null,
          },
        })
        .returning(submissionProjection);

      return submitted;
    }),

  /**
   * Read back the caller's own recommendations, including the ones still in
   * review that the public map deliberately does not show. Without `teamId`
   * this is what the caller authored; with one it is what the workspace shares,
   * which every member including a viewer may read.
   */
  listMySubmissions: protectedProcedure
    .input(
      z
        .object({
          teamId: z.string().uuid().optional(),
          limit: z.number().int().min(1).max(200).default(50),
        })
        .default({})
    )
    .query(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      if (input.teamId) {
        await requireTeamAccess(ctx, input.teamId, userId, false);
      }
      const layerId = await resolveInterventionsLayerId(ctx);

      const ownership = input.teamId
        ? sql`${features.properties} ->> 'submittedByTeamId' = ${input.teamId}`
        : sql`${features.properties} ->> 'submittedByUserId' = ${userId}`;

      return ctx.db
        .select(submissionProjection)
        .from(features)
        .where(and(eq(features.layerId, layerId), ownership))
        .orderBy(desc(features.createdAt))
        .limit(input.limit);
    }),
});
