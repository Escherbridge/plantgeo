import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { and, eq } from "drizzle-orm";
import { adminProcedure, protectedProcedure } from "@/lib/server/trpc/init";
import { teamMembers, teams, users } from "@/lib/server/db/schema";
import { summarizeStrategyActivity } from "@/lib/server/services/community-activity";
import { loadMembership, sessionUserId } from "./shared";

const PARTNER_DIRECTORY_UNAVAILABLE_MESSAGE =
  "Partner discovery is unavailable until verified organizations and access rules are published";

const OPPORTUNITY_WAYPOINTS_UNAVAILABLE_MESSAGE =
  "Opportunity waypoints are not built yet: agri.opportunity_candidate, " +
  "agri.opportunity_waypoint and agri.waypoint_access_review do not exist in any schema";

/** Public/member-facing organization directory, dashboard and platform verification. */
export const directoryProcedures = {
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
      const userId = sessionUserId(ctx);
      const membership = await loadMembership(ctx.db, input.id, userId);
      if (!membership.isMember) {
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
      if (!team) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Organization not found",
        });
      }

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
      const userId = sessionUserId(ctx);
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
      if (!membership) {
        throw new TRPCError({
          code: "FORBIDDEN",
          message: "Not a member of this organization",
        });
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
        .where(eq(teams.id, input.teamId))
        .limit(1);
      if (!team) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Organization not found",
        });
      }

      const members = await ctx.db
        .select({
          teamRole: teamMembers.teamRole,
          name: users.name,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.teamId));

      // Priority zones are a live aggregate over this workspace's own requests;
      // opportunity waypoints have no table to read yet.
      const priorityZones = await summarizeStrategyActivity(ctx.db, {
        userId,
        teamId: input.teamId,
      });

      return {
        team,
        members,
        memberRole: membership.teamRole,
        priorityZones,
        opportunityWaypoints: {
          state: "not_built" as const,
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
      if (!updated) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Organization not found",
        });
      }
      return updated;
    }),
};
