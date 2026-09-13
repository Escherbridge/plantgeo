import { TRPCError } from "@trpc/server";
import { and, desc, eq, sql } from "drizzle-orm";
import { z } from "zod";
import {
  contributorProcedure,
  protectedProcedure,
  publicProcedure,
  router,
  type Context,
} from "@/lib/server/trpc/init";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";
import { features, layers, teamMembers } from "@/lib/server/db/schema";
import { isTeamEditorRole } from "@/lib/server/security/access-control";
import {
  AIR_INTERVENTION_TYPES,
  InterventionCategorySchema,
  LAND_INTERVENTION_TYPES,
  type InterventionType,
} from "@/lib/environmental/intervention";
import {
  countInterventionGeometryPositions,
  getInterventionAreaCapIssue,
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

/**
 * A public strategy request posts straight to the published-equivalent status,
 * skipping the review queue a drawn recommendation enters.
 *
 * This is the exact string `contributions.publishContribution` writes and
 * `geo.intervention_tiles` / `isFeatureVisibleTo` filter on -- deliberately the
 * same value and not a request-only synonym, so a request is visible to a
 * signed-out reader through the one published rule rather than a second one
 * (track `public_strategy_requests_20260913`, OQ-A sub-decision (a): a request
 * is a lighter-weight social ask, not a land-use claim whose accuracy the map's
 * factual record depends on).
 */
const REQUEST_STATUS = "published";

/**
 * The `properties.kind` discriminator separating the two things that now share
 * the `interventions` layer. Absent means `"intervention"`: every row written
 * before 2026-09-13 predates the field and is not backfilled, so readers treat
 * a missing `kind` as a recommendation rather than failing on it.
 */
const REQUEST_KIND = "request";

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

/**
 * Derived from `src/lib/environmental/intervention.ts` rather than re-typed
 * here: a hand-mirrored copy is exactly how the retired `STRATEGY_TYPES` list
 * drifted out of step with `InterventionType` in the first place.
 */
const InterventionTypeSchema = z.enum([
  ...LAND_INTERVENTION_TYPES,
  ...AIR_INTERVENTION_TYPES,
] as [InterventionType, ...InterventionType[]]);

/**
 * The request flow's narrower vocabulary (OQ-D): land types only. An
 * air-category type submitted as a request is a validation error, not a silently
 * re-categorised row.
 */
const LandInterventionTypeSchema = z.enum(
  LAND_INTERVENTION_TYPES as [InterventionType, ...InterventionType[]],
  {
    errorMap: () => ({
      message:
        "A strategy request must name a land-category intervention type; air-category types are not requestable",
    }),
  }
);

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

/** `NOT_FOUND`, never `FORBIDDEN`: an id the caller may not see must not be confirmed to exist. */
const interventionNotVisible = () =>
  new TRPCError({ code: "NOT_FOUND", message: "Intervention not found" });

/**
 * The visibility rule the by-id detail read applies, stated once.
 *
 * It is deliberately the same rule set `listMySubmissions` (own / workspace
 * rows), `listProposed` (the consenting `pending_review` queue) and
 * `interventionSocial.requireVisibleFeature` already enforce, rather than a
 * second one that could drift:
 *   - `published`   -> everyone, signed in or not (it is already on the map);
 *   - own row       -> its submitter, at any status, including `rejected`;
 *   - workspace row -> any member of the submitting team, re-read from the
 *                      database inside this request rather than trusted from
 *                      the session;
 *   - `pending_review` with `publicationConsent = true` -> every SIGNED-IN
 *                      reader, exactly the set `/feed` already shows it to.
 * A signed-out caller passes `userId = null` and so reaches only the first arm.
 */
async function isFeatureVisibleTo(
  ctx: Context,
  row: {
    /** Nullable in the schema; a row with no status is nobody's published row. */
    status: string | null;
    submittedByUserId: string | null;
    submittedByTeamId: string | null;
    publicationConsent: string | null;
  },
  userId: string | null
): Promise<boolean> {
  if (row.status === "published") return true;
  if (!userId) return false;
  if (row.submittedByUserId === userId) return true;
  if (row.submittedByTeamId) {
    const [membership] = await ctx.db
      .select({ userId: teamMembers.userId })
      .from(teamMembers)
      .where(
        and(
          eq(teamMembers.teamId, row.submittedByTeamId),
          eq(teamMembers.userId, userId)
        )
      )
      .limit(1);
    if (membership) return true;
  }
  return (
    row.status === RECOMMENDATION_STATUS && row.publicationConsent === "true"
  );
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
      z
        .object({
          name: z.string().trim().min(3).max(256),
          type: InterventionTypeSchema,
          category: InterventionCategorySchema,
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
        .superRefine((value, context) => {
          const issue = getInterventionAreaCapIssue(value.geometry, value.category);
          if (issue) {
            context.addIssue({
              code: z.ZodIssueCode.custom,
              message: issue,
              path: ["geometry"],
            });
          }
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
            category: input.category,
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
   * Record one signed-in contributor's PUBLIC strategy request -- "this area
   * could use X" -- as a feature that is on the map the moment it is written.
   *
   * It lives here, next to `submitIntervention`, because it is the same table,
   * the same layer, the same geometry validator and the same properties bag; the
   * only three differences are deliberate and each is load-bearing:
   *   - `status` is `REQUEST_STATUS` (`published`), not `RECOMMENDATION_STATUS`.
   *     An ask needs no expert adjudication before anyone may read it, the way a
   *     drawn site whose accuracy the map asserts does (OQ-A sub-decision (a)).
   *   - `properties.kind` is stamped `"request"`, which is what the merged map
   *     layer paints on and what the detail modal labels on.
   *   - the type vocabulary is the land-only subset (OQ-D).
   * `publicationConsent` is NOT one of the differences: a request is exactly as
   * public as a published intervention, so it takes the identical explicit
   * consent gate rather than a weaker one.
   *
   * It replaces `community.submitRequest`, which wrote a non-geospatial,
   * owner/team-private `strategy_requests` row that no map layer could ever
   * draw. `title` there is `name` here, matching `submitIntervention`'s field so
   * one properties bag describes both kinds.
   */
  submitRequest: contributorProcedure
    .input(
      z.object({
        name: z.string().trim().min(3).max(256),
        type: LandInterventionTypeSchema,
        description: z
          .string()
          .trim()
          .max(MAX_SUBMISSION_DESCRIPTION_LENGTH)
          .optional(),
        // The same validator and the same interactive vertex ceiling the drawn
        // submission takes. A request is a Point in practice (the submit form
        // only ever collects a pin), and a Point has no area, so the
        // land-category area cap `submitIntervention` refines on has nothing to
        // say here -- it is applied anyway, below, so an optional future drawn
        // request area cannot slip past it.
        geometry: BoundedInterventionGeometrySchema,
        publicationConsent: z.literal(true),
      }).superRefine((value, context) => {
        const issue = getInterventionAreaCapIssue(value.geometry, "land");
        if (issue) {
          context.addIssue({
            code: z.ZodIssueCode.custom,
            message: issue,
            path: ["geometry"],
          });
        }
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      const layerId = await resolveInterventionsLayerId(ctx);

      const [submitted] = await ctx.db
        .insert(features)
        .values({
          layerId,
          status: REQUEST_STATUS,
          properties: {
            kind: REQUEST_KIND,
            name: input.name,
            type: input.type,
            // Requests are land-category-only, so the category is not asked for
            // and not inferred -- it is the one value it can be.
            category: "land",
            description: input.description ?? null,
            geometry: input.geometry,
            submittedByUserId: userId,
            // No team arm at all: the private/team-scoped visibility the old
            // `getRequests` enforced is the boundary this track removes, and a
            // `submittedByTeamId` written here would quietly re-create it
            // through `isFeatureVisibleTo`'s workspace clause.
            submittedByTeamId: null,
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
          category: sql<string | null>`${features.properties} ->> 'category'`,
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
   * One intervention's whole record, by id -- what the map's click-to-inspect
   * modal opens for a feature that arrived as a Martin tile.
   *
   * Why `publicProcedure` and not `protectedProcedure`: the published layer
   * (`geo.intervention_tiles`, `status = 'published'` only) is already drawn for
   * a signed-out reader, so requiring a session to read what that reader can
   * see on screen would break click-to-inspect for exactly the rows the map
   * publishes. The tier is therefore open and the *row* is gated, inside the
   * procedure, by `isFeatureVisibleTo` below -- which reproduces the visibility
   * `listMySubmissions` / `listProposed` / `interventionSocial`'s
   * `requireVisibleFeature` already enforce, with `userId = null` for a
   * signed-out caller so nothing but `published` is reachable without a session.
   *
   * Every miss is `NOT_FOUND`, never `FORBIDDEN`: a correctly guessed id for a
   * row the caller may not see must not be confirmed to exist (NFR-2).
   *
   * The projection carries `properties -> 'geometry'` in full, not
   * `ST_Centroid` the way `listProposed` does, because the whole point of this
   * read is that a vector tile's geometry is simplified and its column set is
   * whatever the tile function happened to select.
   */
  getInterventionDetail: publicProcedure
    .input(z.object({ featureId: z.string().uuid() }))
    .query(async ({ ctx, input }): Promise<InterventionDetailRecord> => {
      const layerId = await resolveInterventionsLayerId(ctx);
      const userId =
        (ctx.session?.user as { id?: string } | undefined)?.id ?? null;

      const [row] = await ctx.db
        .select({
          id: features.id,
          name: sql<string | null>`${features.properties} ->> 'name'`,
          type: sql<string | null>`${features.properties} ->> 'type'`,
          category: sql<string | null>`${features.properties} ->> 'category'`,
          // NULL on every row written before 2026-09-13, which is read as
          // `"intervention"` below rather than backfilled.
          kind: sql<string | null>`${features.properties} ->> 'kind'`,
          description: sql<
            string | null
          >`${features.properties} ->> 'description'`,
          // The drawn shape as submitted. NULL for a row authored by a path that
          // records no geometry (`proposeIntervention`); the client says so
          // rather than drawing something that was never submitted.
          geometry: sql<
            GeoJSON.Geometry | null
          >`${features.properties} -> 'geometry'`,
          status: features.status,
          reviewNote: features.reviewNote,
          submittedByUserId: sql<
            string | null
          >`${features.properties} ->> 'submittedByUserId'`,
          submittedByTeamId: sql<
            string | null
          >`${features.properties} ->> 'submittedByTeamId'`,
          publicationConsent: sql<
            string | null
          >`${features.properties} ->> 'publicationConsent'`,
          createdAt: features.createdAt,
          updatedAt: features.updatedAt,
        })
        .from(features)
        // `layer_id` first: it prunes the partition, and it also keeps this read
        // from answering for any non-intervention feature that shares the table.
        .where(and(eq(features.layerId, layerId), eq(features.id, input.featureId)))
        .limit(1);

      if (!row || !(await isFeatureVisibleTo(ctx, row, userId))) {
        throw interventionNotVisible();
      }

      return {
        id: row.id,
        name: row.name,
        type: row.type,
        category: row.category,
        kind: row.kind === REQUEST_KIND ? "request" : "intervention",
        // `unknown`, never a plausible-looking default: a NULL status is a data
        // fault, and naming it one is better than showing a reader a standing
        // the row does not actually have.
        status: row.status ?? "unknown",
        description: row.description,
        geometry: row.geometry ?? null,
        submittedByUserId: row.submittedByUserId,
        submittedByTeamId: row.submittedByTeamId,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
        // `reviewNote` is written by `contributions.rejectContribution` and is
        // only meaningful on a rejected row; `castModerationVote` stamps the same
        // column with a non-authoritative vote string, which no reader honours.
        reviewNote: row.status === "rejected" ? row.reviewNote : null,
        hasFullGeometry: Boolean(row.geometry),
      };
    }),

  /** Propose a community intervention (lat/lon + strategy cell); deliberately geometry-less, guarded by a regression test in interventions.test.ts. */
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
            // Absent, never defaulted: until 2026-09-02 an omitted estimate was persisted as
            // 0.15, so a row nobody had estimated was indistinguishable from one somebody had.
            // The key is simply not written when the submitter supplied nothing.
            ...(input.causalTauEst === undefined
              ? {}
              : { causalTauEst: input.causalTauEst }),
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
