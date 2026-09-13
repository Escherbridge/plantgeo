import { TRPCError } from "@trpc/server";
import { and, asc, eq, isNull, sql } from "drizzle-orm";
import { z } from "zod";
import {
  contributorProcedure,
  protectedProcedure,
  router,
  type Context,
} from "@/lib/server/trpc/init";
import {
  featureComments,
  featureLikes,
  features,
  teamMembers,
} from "@/lib/server/db/schema";

/**
 * Likes and flat comments on a `geo.features` row.
 *
 * Deliberately its own router, and deliberately keyed on a bare `featureId`:
 * nothing here is intervention-specific, so `/feed`, the map detail panel and
 * any future content type read one backend rather than growing a second.
 *
 * This module is strictly additive social state. It never reads or writes
 * `features.status` / `features.reviewNote` -- the review vocabulary owned by
 * `contributions.publishContribution` / `rejectContribution`, and shadowed by
 * the non-authoritative `interventions.castModerationVote` /
 * `transitionLifecycleState` pair. `features.status` is consulted here only as
 * a read-side visibility predicate; no procedure in this file issues an UPDATE
 * against `geo.features` at all.
 */

/** Mirrors `submitIntervention`'s `description` bound. */
export const MAX_COMMENT_BODY_LENGTH = 2_000;

const RECOMMENDATION_STATUS = "pending_review";
const PUBLISHED_STATUS = "published";

const commentProjection = {
  id: featureComments.id,
  featureId: featureComments.featureId,
  authorUserId: featureComments.authorUserId,
  body: featureComments.body,
  createdAt: featureComments.createdAt,
};

function currentUserId(session: { user?: unknown }): string {
  const userId = (session.user as { id?: string } | undefined)?.id;
  if (!userId) {
    throw new TRPCError({ code: "UNAUTHORIZED", message: "User ID required" });
  }
  return userId;
}

function platformRole(session: Context["session"]): string | undefined {
  return (session?.user as { platformRole?: string } | undefined)?.platformRole;
}

/** `NOT_FOUND`, never `FORBIDDEN`: an id the caller may not see must not be confirmed to exist. */
const featureNotVisible = () =>
  new TRPCError({ code: "NOT_FOUND", message: "Intervention not found" });

/**
 * The one authorization gate every procedure here runs first.
 *
 * It reproduces exactly the visibility `listMySubmissions` / `listProposed`
 * already enforce, rather than inventing a second rule set:
 *   - `published`   -> visible to every signed-in caller (it is on the map);
 *   - own row       -> visible to its submitter at any status;
 *   - team row      -> visible to any member of the submitting workspace,
 *                      re-read from the database inside this request;
 *   - `pending_review` with `publicationConsent = true` -> the shared review
 *                      queue `listProposed` publishes to every signed-in reader.
 * Anything else (another contributor's draft without consent, a rejected row)
 * is not visible, and says so as `NOT_FOUND`.
 */
async function requireVisibleFeature(
  ctx: Context,
  featureId: string,
  userId: string
): Promise<{ id: string }> {
  const [feature] = await ctx.db
    .select({
      id: features.id,
      status: features.status,
      submittedByUserId: sql<
        string | null
      >`${features.properties} ->> 'submittedByUserId'`,
      submittedByTeamId: sql<
        string | null
      >`${features.properties} ->> 'submittedByTeamId'`,
      publicationConsent: sql<
        string | null
      >`${features.properties} ->> 'publicationConsent'`,
    })
    .from(features)
    .where(eq(features.id, featureId))
    .limit(1);

  if (!feature) {
    throw featureNotVisible();
  }
  if (feature.status === PUBLISHED_STATUS) {
    return { id: feature.id };
  }
  if (feature.submittedByUserId === userId) {
    return { id: feature.id };
  }
  if (feature.submittedByTeamId) {
    const [membership] = await ctx.db
      .select({ userId: teamMembers.userId })
      .from(teamMembers)
      .where(
        and(
          eq(teamMembers.teamId, feature.submittedByTeamId),
          eq(teamMembers.userId, userId)
        )
      )
      .limit(1);
    if (membership) {
      return { id: feature.id };
    }
  }
  if (
    feature.status === RECOMMENDATION_STATUS &&
    feature.publicationConsent === "true"
  ) {
    return { id: feature.id };
  }
  throw featureNotVisible();
}

async function countLikes(ctx: Context, featureId: string): Promise<number> {
  const [row] = await ctx.db
    // `::int`, not a bare `count(*)`: postgres-js hands a bigint back as a
    // string, and a string count would reach the client as one.
    .select({ total: sql<number>`count(*)::int` })
    .from(featureLikes)
    .where(eq(featureLikes.featureId, featureId));
  return Number(row?.total ?? 0);
}

export const interventionSocialRouter = router({
  /**
   * Add or remove the caller's own like. Idempotent per call: two calls with
   * the same caller and feature land back where they started.
   */
  toggleLike: contributorProcedure
    .input(z.object({ featureId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      await requireVisibleFeature(ctx, input.featureId, userId);

      const [existing] = await ctx.db
        .select({ id: featureLikes.id })
        .from(featureLikes)
        .where(
          and(
            eq(featureLikes.featureId, input.featureId),
            eq(featureLikes.userId, userId)
          )
        )
        .limit(1);

      if (existing) {
        await ctx.db.delete(featureLikes).where(eq(featureLikes.id, existing.id));
      } else {
        // The unique constraint, not this branch, is what makes a double-click
        // safe; `onConflictDoNothing` keeps the race a no-op rather than a 500.
        await ctx.db
          .insert(featureLikes)
          .values({ featureId: input.featureId, userId })
          .onConflictDoNothing();
      }

      return {
        liked: !existing,
        count: await countLikes(ctx, input.featureId),
      };
    }),

  /** The caller's own like state plus the current total, without writing anything. */
  getLikeState: protectedProcedure
    .input(z.object({ featureId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      await requireVisibleFeature(ctx, input.featureId, userId);

      const [existing] = await ctx.db
        .select({ id: featureLikes.id })
        .from(featureLikes)
        .where(
          and(
            eq(featureLikes.featureId, input.featureId),
            eq(featureLikes.userId, userId)
          )
        )
        .limit(1);

      return {
        liked: Boolean(existing),
        count: await countLikes(ctx, input.featureId),
      };
    }),

  /**
   * One feature's comment thread, oldest first, page by page.
   *
   * Offset paging rather than a cursor because the thread is flat, append-only
   * and ordered by a column nothing rewrites; `nextOffset` is null on the last
   * page so a caller never issues the empty trailing request.
   */
  listComments: protectedProcedure
    .input(
      z.object({
        featureId: z.string().uuid(),
        limit: z.number().int().min(1).max(100).default(50),
        offset: z.number().int().min(0).default(0),
      })
    )
    .query(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      await requireVisibleFeature(ctx, input.featureId, userId);

      const comments = await ctx.db
        .select(commentProjection)
        .from(featureComments)
        .where(
          and(
            eq(featureComments.featureId, input.featureId),
            // Soft-deleted rows stay on disk for the audit trail and are never served.
            isNull(featureComments.deletedAt)
          )
        )
        .orderBy(asc(featureComments.createdAt))
        .limit(input.limit)
        .offset(input.offset);

      return {
        comments,
        nextOffset:
          comments.length === input.limit ? input.offset + comments.length : null,
      };
    }),

  /** Post one flat comment. Visible immediately: comments have no review queue. */
  postComment: contributorProcedure
    .input(
      z.object({
        featureId: z.string().uuid(),
        body: z.string().trim().min(1).max(MAX_COMMENT_BODY_LENGTH),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      await requireVisibleFeature(ctx, input.featureId, userId);

      const [posted] = await ctx.db
        .insert(featureComments)
        .values({
          featureId: input.featureId,
          authorUserId: userId,
          body: input.body,
        })
        .returning(commentProjection);

      return posted;
    }),

  /**
   * Soft-delete one comment: its own author, or an `expert`/`admin` platform
   * role acting as a moderator. The row is stamped, never removed, so a
   * moderation decision stays auditable.
   */
  deleteComment: contributorProcedure
    .input(z.object({ commentId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);

      const [comment] = await ctx.db
        .select({
          id: featureComments.id,
          authorUserId: featureComments.authorUserId,
          deletedAt: featureComments.deletedAt,
        })
        .from(featureComments)
        .where(eq(featureComments.id, input.commentId))
        .limit(1);

      if (!comment || comment.deletedAt) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Comment not found" });
      }

      const role = platformRole(ctx.session);
      const isModerator = role === "expert" || role === "admin";
      if (comment.authorUserId !== userId && !isModerator) {
        throw new TRPCError({
          code: "FORBIDDEN",
          message: "Only the comment's author or a moderator may delete it",
        });
      }

      const [deleted] = await ctx.db
        .update(featureComments)
        .set({ deletedAt: new Date(), deletedByUserId: userId })
        .where(
          and(
            eq(featureComments.id, input.commentId),
            isNull(featureComments.deletedAt)
          )
        )
        .returning({
          id: featureComments.id,
          deletedAt: featureComments.deletedAt,
        });

      // The SELECT above prunes; it does not prove the row survived to the
      // UPDATE. A concurrent delete matching nothing must error, not resolve.
      if (!deleted) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Comment not found" });
      }

      return deleted;
    }),
});
