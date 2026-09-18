import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { and, eq, sql } from "drizzle-orm";
import { protectedProcedure } from "@/lib/server/trpc/init";
import { teamMembers, teams, users } from "@/lib/server/db/schema";
import {
  identityFromSession,
  isPlatformAdmin,
  wouldRemoveLastOwner,
} from "@/lib/server/security/access-control";
import {
  loadMembership,
  requireTeamAccess,
  sessionUserId,
} from "./shared";

/** Membership listing, removal, role changes and self-service leave. */
export const membershipProcedures = {
  listMembers: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await requireTeamAccess(ctx.db, input.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        message: "Not a member of this organization",
      });
      return ctx.db
        .select({
          userId: teamMembers.userId,
          name: users.name,
          email: users.email,
          teamRole: teamMembers.teamRole,
          joinedAt: teamMembers.joinedAt,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.teamId));
    }),

  removeMember: protectedProcedure
    .input(z.object({ teamId: z.string().uuid(), userId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const callerId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      await ctx.db.transaction(async (transaction) => {
        await transaction.execute(
          sql`SELECT 1 FROM ${teams} WHERE ${teams.id} = ${input.teamId} FOR UPDATE`
        );
        const [callerMembership] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, callerId)
            )
          )
          .limit(1);
        if (!platformAdmin && callerMembership?.role !== "owner") {
          throw new TRPCError({ code: "FORBIDDEN" });
        }

        const [target] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          )
          .limit(1);
        if (!target) throw new TRPCError({ code: "NOT_FOUND" });
        if (target.role === "owner") {
          const [owners] = await transaction
            .select({ count: sql<number>`COUNT(*)::int` })
            .from(teamMembers)
            .where(
              and(
                eq(teamMembers.teamId, input.teamId),
                eq(teamMembers.teamRole, "owner")
              )
            );
          if (wouldRemoveLastOwner(target.role, null, owners?.count ?? 0)) {
            throw new TRPCError({
              code: "CONFLICT",
              message: "A team must retain at least one owner",
            });
          }
        }

        await transaction
          .delete(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          );
        await transaction
          .update(users)
          .set({ activeTeamId: null })
          .where(
            and(
              eq(users.id, input.userId),
              eq(users.activeTeamId, input.teamId)
            )
          );
      });
      return { success: true };
    }),

  updateMemberRole: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        userId: z.string().uuid(),
        teamRole: z.enum(["owner", "member", "viewer"]),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const callerId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      return ctx.db.transaction(async (transaction) => {
        await transaction.execute(
          sql`SELECT 1 FROM ${teams} WHERE ${teams.id} = ${input.teamId} FOR UPDATE`
        );
        const [callerMembership] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, callerId)
            )
          )
          .limit(1);
        if (!platformAdmin && callerMembership?.role !== "owner") {
          throw new TRPCError({ code: "FORBIDDEN" });
        }

        const [target] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          )
          .limit(1);
        if (!target) throw new TRPCError({ code: "NOT_FOUND" });
        if (target.role === "owner" && input.teamRole !== "owner") {
          const [owners] = await transaction
            .select({ count: sql<number>`COUNT(*)::int` })
            .from(teamMembers)
            .where(
              and(
                eq(teamMembers.teamId, input.teamId),
                eq(teamMembers.teamRole, "owner")
              )
            );
          if (
            wouldRemoveLastOwner(
              target.role,
              input.teamRole,
              owners?.count ?? 0
            )
          ) {
            throw new TRPCError({
              code: "CONFLICT",
              message: "A team must retain at least one owner",
            });
          }
        }

        const [updated] = await transaction
          .update(teamMembers)
          .set({ teamRole: input.teamRole })
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          )
          .returning();
        return updated;
      });
    }),

  leaveTeam: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await ctx.db.transaction(async (transaction) => {
        await transaction.execute(
          sql`SELECT 1 FROM ${teams} WHERE ${teams.id} = ${input.teamId} FOR UPDATE`
        );
        const membership = await loadMembership(
          transaction,
          input.teamId,
          userId
        );
        if (!membership.isMember) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "Not a member of this organization",
          });
        }
        if (membership.role === "owner") {
          const [owners] = await transaction
            .select({ count: sql<number>`COUNT(*)::int` })
            .from(teamMembers)
            .where(
              and(
                eq(teamMembers.teamId, input.teamId),
                eq(teamMembers.teamRole, "owner")
              )
            );
          if (wouldRemoveLastOwner("owner", null, owners?.count ?? 0)) {
            throw new TRPCError({
              code: "CONFLICT",
              message:
                "Transfer ownership before leaving: an organization must retain at least one owner",
            });
          }
        }

        await transaction
          .delete(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, userId)
            )
          );
        await transaction
          .update(users)
          .set({ activeTeamId: null })
          .where(
            and(eq(users.id, userId), eq(users.activeTeamId, input.teamId))
          );
      });
      return { success: true };
    }),
};
