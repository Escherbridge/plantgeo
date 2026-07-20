import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { eq, and, sql } from "drizzle-orm";
import {
  router,
  protectedProcedure,
  contributorProcedure,
  adminProcedure,
} from "@/lib/server/trpc/init";
import { teams, teamMembers, users } from "@/lib/server/db/schema";
import {
  canInviteTeamRole,
  identityFromSession,
  isPlatformAdmin,
  wouldRemoveLastOwner,
} from "@/lib/server/security/access-control";

const orgTypeSchema = z.enum([
  "nonprofit",
  "cooperative",
  "business",
  "individual",
  "government",
]);

const PARTNER_DIRECTORY_UNAVAILABLE_MESSAGE =
  "Partner discovery is unavailable until verified organizations and access rules are published";

const OPPORTUNITY_WAYPOINTS_UNAVAILABLE_MESSAGE =
  "Opportunity waypoints are inactive until a reviewed, workspace-scoped warehouse publication is available";

export const teamsRouter = router({
  // ─── Existing procedures (preserved) ──────────────────────────────────

  listMyTeams: protectedProcedure.query(async ({ ctx }) => {
    const userId = (ctx.session.user as { id: string }).id;
    const rows = await ctx.db
      .select({ team: teams, role: teamMembers.teamRole })
      .from(teamMembers)
      .innerJoin(teams, eq(teamMembers.teamId, teams.id))
      .where(eq(teamMembers.userId, userId));
    return rows;
  }),

  inviteMember: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        userId: z.string().uuid(),
        teamRole: z.enum(["member", "viewer"]).default("member"),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const callerId = (ctx.session.user as { id: string }).id;
      const [callerMembership] = await ctx.db
        .select()
        .from(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.teamId),
            eq(teamMembers.userId, callerId)
          )
        )
        .limit(1);
      if (
        !isPlatformAdmin(identityFromSession(ctx.session)) &&
        !canInviteTeamRole(callerMembership?.teamRole, input.teamRole)
      ) {
        throw new TRPCError({
          code: "FORBIDDEN",
          message: "Not authorized to delegate this team role",
        });
      }
      const [member] = await ctx.db
        .insert(teamMembers)
        .values({
          teamId: input.teamId,
          userId: input.userId,
          teamRole: input.teamRole,
        })
        .returning();
      return member;
    }),

  removeMember: protectedProcedure
    .input(z.object({ teamId: z.string().uuid(), userId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const callerId = (ctx.session.user as { id: string }).id;
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
      const callerId = (ctx.session.user as { id: string }).id;
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

  // ─── New procedures ────────────────────────────────────────────────────

  createTeam: contributorProcedure
    .input(
      z.object({
        name: z.string().min(1).max(100),
        slug: z.string().min(1).max(100).optional(),
        description: z.string().optional(),
        orgType: orgTypeSchema.optional(),
        specialties: z.array(z.string()).optional(),
        website: z.string().url().optional(),
        serviceArea: z.record(z.unknown()).optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id: string }).id;
      const [team] = await ctx.db
        .insert(teams)
        .values({
          name: input.name,
          slug: input.slug,
          description: input.description,
          orgType: input.orgType,
          specialties: input.specialties ?? [],
          website: input.website,
          serviceArea: input.serviceArea ?? null,
          createdBy: userId,
        })
        .returning();
      await ctx.db.insert(teamMembers).values({
        teamId: team.id,
        userId,
        teamRole: "owner",
      });
      return team;
    }),

  updateTeam: contributorProcedure
    .input(
      z.object({
        id: z.string().uuid(),
        name: z.string().min(1).max(100).optional(),
        slug: z.string().min(1).max(100).optional(),
        description: z.string().optional(),
        orgType: orgTypeSchema.optional(),
        specialties: z.array(z.string()).optional(),
        website: z.string().url().optional().nullable(),
        serviceArea: z.record(z.unknown()).optional().nullable(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id: string }).id;
      // Ensure caller is owner or member of this team
      const [membership] = await ctx.db
        .select()
        .from(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.id),
            eq(teamMembers.userId, userId)
          )
        )
        .limit(1);
      if (!membership || !["owner", "member"].includes(membership.teamRole ?? "")) {
        throw new Error("Not authorized to update this team");
      }
      const { id, ...fields } = input;
      const [updated] = await ctx.db
        .update(teams)
        .set(fields)
        .where(eq(teams.id, id))
        .returning();
      return updated;
    }),

  getTeamsInBbox: protectedProcedure
    .input(z.object({ bbox: z.string() }))
    .query(() => {
      throw new TRPCError({
        code: "PRECONDITION_FAILED",
        message: PARTNER_DIRECTORY_UNAVAILABLE_MESSAGE,
      });
    }),

  getTeamProfile: protectedProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id: string }).id;
      const [membership] = await ctx.db
        .select({ teamId: teamMembers.teamId })
        .from(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.id),
            eq(teamMembers.userId, userId)
          )
        )
        .limit(1);
      if (!membership) {
        throw new TRPCError({ code: "FORBIDDEN", message: "Not a team member" });
      }

      const [team] = await ctx.db
        .select({
          id: teams.id,
          name: teams.name,
          slug: teams.slug,
          description: teams.description,
          orgType: teams.orgType,
          specialties: teams.specialties,
          website: teams.website,
          serviceArea: teams.serviceArea,
          isVerified: teams.isVerified,
          createdAt: teams.createdAt,
        })
        .from(teams)
        .where(eq(teams.id, input.id))
        .limit(1);
      if (!team) throw new Error("Team not found");

      const members = await ctx.db
        .select({
          teamRole: teamMembers.teamRole,
          name: users.name,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.id));

      return { ...team, members, memberCount: members.length };
    }),

  getTeamDashboard: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id: string }).id;
      // Verify membership
      const [membership] = await ctx.db
        .select()
        .from(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.teamId),
            eq(teamMembers.userId, userId)
          )
        )
        .limit(1);
      if (!membership) throw new Error("Not a member of this team");

      const [team] = await ctx.db
        .select({
          id: teams.id,
          name: teams.name,
          slug: teams.slug,
          description: teams.description,
          orgType: teams.orgType,
          specialties: teams.specialties,
          website: teams.website,
          serviceArea: teams.serviceArea,
          isVerified: teams.isVerified,
          createdAt: teams.createdAt,
        })
        .from(teams)
        .where(eq(teams.id, input.teamId))
        .limit(1);
      if (!team) throw new Error("Team not found");

      const members = await ctx.db
        .select({
          teamRole: teamMembers.teamRole,
          name: users.name,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.teamId));

      // Opportunity waypoints stay inactive until reviewed publication is available.
      return {
        team,
        members,
        memberRole: membership.teamRole,
        priorityZones: [] as Array<{
          id: string;
          strategyType: string;
          requestCount: number;
          totalVotes: number;
        }>,
        opportunityWaypoints: {
          state: "inactive" as const,
          message: OPPORTUNITY_WAYPOINTS_UNAVAILABLE_MESSAGE,
        },
      };
    }),

  // ─── Phase 6: Admin verification ──────────────────────────────────────

  verifyTeam: adminProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const [updated] = await ctx.db
        .update(teams)
        .set({ isVerified: true, verifiedAt: new Date() })
        .where(eq(teams.id, input.teamId))
        .returning();
      return updated;
    }),
});
