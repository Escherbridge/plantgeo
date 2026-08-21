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
            // Persisted, not merely validated: `listProposed` filters on a
            // consent flag recorded against the row rather than inferring it
            // from which code path happened to create it. Rows written by the
            // machine-ingress route carry no flag and so stay out of the feed,
            // and any row predating this field fails closed for the same reason.
            publicationConsent: input.publicationConsent,
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

  /**
   * The `/feed` read: every recommendation still awaiting an expert decision,
   * across all contributors.
   *
   * Three deliberate bounds, because this is the one procedure here that shows
   * one account's submission to another account:
   *   - `protectedProcedure`, not public. Consent covers publication to the
   *     platform, not publication to the open internet.
   *   - `publicationConsent` must be recorded true on the row itself.
   *   - only `pending_review`. Published rows are already on the map through
   *     `geo.intervention_tiles`, and rejected rows are nobody else's business.
   *
   * The centroid, rather than `properties.geometry`, is what a feed row needs:
   * it is enough to fly the camera to the site without shipping every parcel
   * outline to every signed-in reader.
   */
  listProposed: protectedProcedure
    .input(
      z
        .object({
          type: InterventionTypeSchema.optional(),
          limit: z.number().int().min(1).max(100).default(50),
        })
        .default({})
    )
    .query(async ({ ctx, input }) => {
      const layerId = await resolveInterventionsLayerId(ctx);

      return ctx.db
        .select({
          id: features.id,
          name: sql<string | null>`${features.properties} ->> 'name'`,
          type: sql<string | null>`${features.properties} ->> 'type'`,
          description: sql<
            string | null
          >`${features.properties} ->> 'description'`,
          // NULL when `geom` is NULL; the client renders those rows without a
          // map link rather than dropping them.
          longitude: sql<
            number | null
          >`ST_X(ST_Centroid(${features.geom}))`,
          latitude: sql<number | null>`ST_Y(ST_Centroid(${features.geom}))`,
          createdAt: features.createdAt,
        })
        .from(features)
        .where(
          and(
            eq(features.layerId, layerId),
            eq(features.status, RECOMMENDATION_STATUS),
            sql`${features.properties} ->> 'publicationConsent' = 'true'`,
            input.type
              ? sql`${features.properties} ->> 'type' = ${input.type}`
              : undefined
          )
        )
        .orderBy(desc(features.createdAt))
        .limit(input.limit);
    }),

  /**
   * Propose a new community intervention linked to a MapLibre click & ML strategy cell.
   */
  proposeIntervention: contributorProcedure
    .input(
      z.object({
        title: z.string().min(3).max(255),
        strategyType: z.string(),
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
        cellId: z.string().optional(),
        causalTauEst: z.number().optional(),
        description: z.string().optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      const layerId = await resolveInterventionsLayerId(ctx);

      const [proposed] = await ctx.db
        .insert(features)
        .values({
          layerId,
          status: "proposed",
          properties: {
            name: input.title,
            type: input.strategyType,
            description: input.description ?? null,
            cellId: input.cellId ?? null,
            causalTauEst: input.causalTauEst ?? 0.15,
            submittedByUserId: userId,
            publicationConsent: true,
          },
        })
        .returning(submissionProjection);

      return proposed;
    }),

  /**
   * Expert voting procedure for moderation queue.
   */
  castModerationVote: contributorProcedure
    .input(
      z.object({
        interventionId: z.string().uuid(),
        vote: z.enum(["approve", "reject", "request_revision"]),
        note: z.string().optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const role = (ctx.session?.user as { platformRole?: string } | undefined)?.platformRole;
      if (!role || !["expert", "admin"].includes(role)) {
        throw new TRPCError({ code: "FORBIDDEN", message: "Expert or Admin role required" });
      }

      const [feature] = await ctx.db
        .select()
        .from(features)
        .where(eq(features.id, input.interventionId))
        .limit(1);

      if (!feature) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Intervention not found" });
      }

      const newStatus = input.vote === "approve" ? "approved" : input.vote === "reject" ? "rejected" : "pending_review";

      const [updated] = await ctx.db
        .update(features)
        .set({
          status: newStatus,
          reviewNote: input.note ?? `Vote cast: ${input.vote}`,
          updatedAt: new Date(),
        })
        // `layer_id` pins the update to the one partition already found above,
        // instead of an update-by-id probing every partition.
        .where(
          and(eq(features.id, input.interventionId), eq(features.layerId, feature.layerId))
        )
        .returning(submissionProjection);

      // The SELECT above prunes; it does not prove the row survived to the UPDATE. A delete
      // racing between the two matches nothing, and that must error rather than resolve.
      if (!updated) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Intervention not found" });
      }

      return updated;
    }),

  /**
   * State machine transition for intervention lifecycle (`proposed` -> `approved` -> `active` -> `monitored`).
   */
  transitionLifecycleState: contributorProcedure
    .input(
      z.object({
        interventionId: z.string().uuid(),
        targetState: z.enum(["proposed", "approved", "active", "monitored"]),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const role = (ctx.session?.user as { platformRole?: string } | undefined)?.platformRole;
      if (!role || !["expert", "admin"].includes(role)) {
        throw new TRPCError({ code: "FORBIDDEN", message: "Expert or Admin role required" });
      }

      // Resolved first so the update below can pin `layer_id` and touch one
      // partition instead of probing every partition for a bare `id` match.
      const [feature] = await ctx.db
        .select({ layerId: features.layerId })
        .from(features)
        .where(eq(features.id, input.interventionId))
        .limit(1);
      if (!feature) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Intervention not found" });
      }

      const [updated] = await ctx.db
        .update(features)
        .set({
          status: input.targetState,
          updatedAt: new Date(),
        })
        .where(
          and(eq(features.id, input.interventionId), eq(features.layerId, feature.layerId))
        )
        .returning(submissionProjection);

      // Kept from before the pre-flight SELECT existed: the SELECT prunes the UPDATE to one
      // partition, it does not stand in for checking that the UPDATE matched a row.
      if (!updated) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Intervention not found" });
      }

      return updated;
    }),
});
