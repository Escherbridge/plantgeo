import { TRPCError } from "@trpc/server";
import { z } from "zod";
import {
  contributorProcedure,
  protectedProcedure,
  router,
  type Context,
} from "@/lib/server/trpc/init";
import {
  requestVotes,
  strategyRequests,
  teamMembers,
} from "@/lib/server/db/schema";
import { and, eq, isNull, sql } from "drizzle-orm";
import { isTeamEditorRole } from "@/lib/server/security/access-control";
import { summarizeStrategyActivity } from "@/lib/server/services/community-activity";

const bboxSchema = z.string().regex(
  /^-?\d+\.?\d*,-?\d+\.?\d*,-?\d+\.?\d*,-?\d+\.?\d*$/,
  'Invalid bbox format: expected "west,south,east,north"'
);

const STRATEGY_TYPES = [
  "keyline",
  "silvopasture",
  "reforestation",
  "biochar",
  "water_harvesting",
  "cover_cropping",
] as const;

const requestProjection = {
  id: strategyRequests.id,
  strategyType: strategyRequests.strategyType,
  title: strategyRequests.title,
  description: strategyRequests.description,
  lat: strategyRequests.lat,
  lon: strategyRequests.lon,
  status: strategyRequests.status,
  voteCount: strategyRequests.voteCount,
  createdAt: strategyRequests.createdAt,
};

function currentUserId(session: { user?: unknown }): string {
  const userId = (session.user as { id?: string } | undefined)?.id;
  if (!userId) {
    throw new TRPCError({ code: "UNAUTHORIZED", message: "User ID required" });
  }
  return userId;
}

/** Verifies the authenticated account's current access to a private workspace. */
async function requireTeamAccess(
  ctx: Context,
  teamId: string,
  userId: string,
  requireEditor: boolean,
  notFoundMessage = "Partner workspace not found"
) {
  const [membership] = await ctx.db
    .select({ teamRole: teamMembers.teamRole })
    .from(teamMembers)
    .where(and(eq(teamMembers.teamId, teamId), eq(teamMembers.userId, userId)))
    .limit(1);

  if (!membership || (requireEditor && !isTeamEditorRole(membership.teamRole))) {
    throw new TRPCError({
      code: "NOT_FOUND",
      message: notFoundMessage,
    });
  }
  return membership;
}

export const communityRouter = router({
  submitRequest: contributorProcedure
    .input(
      z.object({
        strategyType: z.enum(STRATEGY_TYPES),
        title: z.string().min(3).max(200),
        description: z.string().optional(),
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
        teamId: z.string().uuid().optional(),
        locationConsent: z.literal(true),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      if (input.teamId) {
        await requireTeamAccess(ctx, input.teamId, userId, true);
      }
      const [inserted] = await ctx.db
        .insert(strategyRequests)
        .values({
          userId,
          teamId: input.teamId ?? null,
          strategyType: input.strategyType,
          title: input.title,
          description: input.description ?? null,
          lat: input.lat,
          lon: input.lon,
          status: "open",
          voteCount: 0,
        })
        .returning(requestProjection);
      return inserted;
    }),

  voteOnRequest: contributorProcedure
    .input(z.object({ requestId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      const [request] = await ctx.db
        .select({ teamId: strategyRequests.teamId })
        .from(strategyRequests)
        .where(eq(strategyRequests.id, input.requestId))
        .limit(1);
      if (!request?.teamId) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Partner workspace request not found",
        });
      }
      await requireTeamAccess(
        ctx,
        request.teamId,
        userId,
        false,
        "Partner workspace request not found"
      );

      const inserted = await ctx.db
        .insert(requestVotes)
        .values({
          requestId: input.requestId,
          userId,
        })
        .onConflictDoNothing()
        .returning();

      if (inserted.length > 0) {
        await ctx.db
          .update(strategyRequests)
          .set({ voteCount: sql`${strategyRequests.voteCount} + 1` })
          .where(
            and(
              eq(strategyRequests.id, input.requestId),
              eq(strategyRequests.teamId, request.teamId)
            )
          );
      }

      const [result] = await ctx.db
        .select({ voteCount: strategyRequests.voteCount })
        .from(strategyRequests)
        .where(eq(strategyRequests.id, input.requestId));

      return { voteCount: result?.voteCount ?? 0 };
    }),

  getRequests: protectedProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(), // "west,south,east,north"
        strategyType: z.enum(STRATEGY_TYPES).optional(),
        teamId: z.string().uuid().optional(),
        limit: z.number().int().min(1).max(200).default(50),
      })
    )
    .query(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      const conditions = input.teamId
        ? [eq(strategyRequests.teamId, input.teamId)]
        : [
            eq(strategyRequests.userId, userId),
            isNull(strategyRequests.teamId),
          ];

      if (input.teamId) {
        await requireTeamAccess(ctx, input.teamId, userId, false);
      }

      if (input.strategyType) {
        conditions.push(eq(strategyRequests.strategyType, input.strategyType));
      }

      if (input.bbox) {
        const parts = input.bbox.split(",").map(Number);
        if (parts.length === 4) {
          const [west, south, east, north] = parts;
          conditions.push(
            sql`${strategyRequests.lon} >= ${west}`,
            sql`${strategyRequests.lon} <= ${east}`,
            sql`${strategyRequests.lat} >= ${south}`,
            sql`${strategyRequests.lat} <= ${north}`
          );
        }
      }

      const rows = await ctx.db
        .select(requestProjection)
        .from(strategyRequests)
        .where(and(...conditions))
        .orderBy(sql`${strategyRequests.voteCount} DESC`)
        .limit(input.limit);

      return rows;
    }),

  /** Per-strategy totals over the requests the caller may already read. */
  getPriorityZones: protectedProcedure
    .input(
      z.object({
        strategyType: z.string().optional(),
        teamId: z.string().uuid().optional(),
      })
    )
    .query(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      if (input.teamId) {
        await requireTeamAccess(ctx, input.teamId, userId, false);
      }
      return summarizeStrategyActivity(ctx.db, {
        userId,
        teamId: input.teamId,
        strategyType: input.strategyType,
      });
    }),

  getRequestById: protectedProcedure
    .input(
      z.object({ id: z.string().uuid(), teamId: z.string().uuid().optional() })
    )
    .query(async ({ ctx, input }) => {
      const userId = currentUserId(ctx.session);
      if (input.teamId) {
        await requireTeamAccess(ctx, input.teamId, userId, false);
      }
      const result = await ctx.db
        .select(requestProjection)
        .from(strategyRequests)
        .where(
          and(
            eq(strategyRequests.id, input.id),
            input.teamId
              ? eq(strategyRequests.teamId, input.teamId)
              : and(
                  eq(strategyRequests.userId, userId),
                  isNull(strategyRequests.teamId)
                )
          )
        );
      return result[0] ?? null;
    }),
});
