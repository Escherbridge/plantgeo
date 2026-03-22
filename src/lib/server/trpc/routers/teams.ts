import { z } from "zod";
import { eq, and, sql } from "drizzle-orm";
import {
  router,
  publicProcedure,
  protectedProcedure,
  contributorProcedure,
  adminProcedure,
} from "@/lib/server/trpc/init";
import { teams, teamMembers, users, priorityZones } from "@/lib/server/db/schema";

const orgTypeSchema = z.enum([
  "nonprofit",
  "cooperative",
  "business",
  "individual",
  "government",
]);

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
        teamRole: z.enum(["owner", "member", "viewer"]).default("member"),
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
      if (!callerMembership || !["owner", "member"].includes(callerMembership.teamRole ?? "")) {
        throw new Error("Not authorized to invite members");
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
      if (!callerMembership || callerMembership.teamRole !== "owner") {
        throw new Error("Not authorized to remove members");
      }
      await ctx.db
        .delete(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.teamId),
            eq(teamMembers.userId, input.userId)
          )
        );
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
      if (!callerMembership || callerMembership.teamRole !== "owner") {
        throw new Error("Not authorized to update roles");
      }
      const [updated] = await ctx.db
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

  getTeamsInBbox: publicProcedure
    .input(z.object({ bbox: z.string() }))
    .query(async ({ ctx, input }) => {
      // bbox format: "minLon,minLat,maxLon,maxLat"
      const parts = input.bbox.split(",").map(Number);
      if (parts.length !== 4 || parts.some(isNaN)) {
        throw new Error("Invalid bbox format. Expected: minLon,minLat,maxLon,maxLat");
      }
      const [minLon, minLat, maxLon, maxLat] = parts;

      // Return all teams that have a serviceArea set, then filter client-side
      // (PostGIS ST_Intersects requires geometry type; we store as jsonb)
      const rows = await ctx.db
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
          memberCount: sql<number>`(
            SELECT COUNT(*)::int FROM team_members tm WHERE tm.team_id = ${teams.id}
          )`,
        })
        .from(teams)
        .where(sql`${teams.serviceArea} IS NOT NULL`);

      // Client-side bbox filter using centroid of bounding box stored in serviceArea
      return rows.filter((team) => {
        if (!team.serviceArea) return false;
        try {
          const geojson = team.serviceArea as {
            type: string;
            coordinates?: number[][][];
            bbox?: number[];
          };
          // Try to use bbox field if present
          if (geojson.bbox && geojson.bbox.length === 4) {
            const [gMinLon, gMinLat, gMaxLon, gMaxLat] = geojson.bbox;
            return !(gMaxLon < minLon || gMinLon > maxLon || gMaxLat < minLat || gMinLat > maxLat);
          }
          // For Polygon, compute rough centroid from first ring
          if (geojson.type === "Polygon" && geojson.coordinates?.[0]) {
            const ring = geojson.coordinates[0];
            const centLon = ring.reduce((s, c) => s + c[0], 0) / ring.length;
            const centLat = ring.reduce((s, c) => s + c[1], 0) / ring.length;
            return centLon >= minLon && centLon <= maxLon && centLat >= minLat && centLat <= maxLat;
          }
          return true;
        } catch {
          return true;
        }
      });
    }),

  getTeamProfile: publicProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const [team] = await ctx.db
        .select()
        .from(teams)
        .where(eq(teams.id, input.id))
        .limit(1);
      if (!team) throw new Error("Team not found");

      const members = await ctx.db
        .select({
          userId: teamMembers.userId,
          teamRole: teamMembers.teamRole,
          joinedAt: teamMembers.joinedAt,
          name: users.name,
          email: users.email,
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
        .select()
        .from(teams)
        .where(eq(teams.id, input.teamId))
        .limit(1);
      if (!team) throw new Error("Team not found");

      const members = await ctx.db
        .select({
          userId: teamMembers.userId,
          teamRole: teamMembers.teamRole,
          joinedAt: teamMembers.joinedAt,
          name: users.name,
          email: users.email,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.teamId));

      // Fetch priority zones — filter to those within service area bbox if present
      let zones: (typeof priorityZones.$inferSelect)[] = [];
      if (team.serviceArea) {
        zones = await ctx.db.select().from(priorityZones).limit(50);
        // Further client-side filter can be applied by the consumer
      }

      return {
        team,
        members,
        memberRole: membership.teamRole,
        priorityZones: zones,
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
