import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { eq } from "drizzle-orm";
import {
  contributorProcedure,
  protectedProcedure,
} from "@/lib/server/trpc/init";
import { teamMembers, teams, users } from "@/lib/server/db/schema";
import { identityFromSession, isPlatformAdmin, isTeamEditorRole } from "@/lib/server/security/access-control";
import {
  generateUniqueSlug,
  normalizeSlug,
  SlugGenerationError,
} from "@/lib/server/security/org-slug";
import { isUniqueConstraintViolation } from "@/lib/server/security/registration";
import {
  adoptActiveTeam,
  loadMembership,
  orgTypeSchema,
  requireTeamAccess,
  sessionUserId,
} from "./shared";

/** Organization CRUD and the caller's active-organization pointer. */
export const organizationProcedures = {
  listMyTeams: protectedProcedure.query(async ({ ctx }) => {
    const userId = sessionUserId(ctx);
    const rows = await ctx.db
      .select({ team: teams, role: teamMembers.teamRole })
      .from(teamMembers)
      .innerJoin(teams, eq(teamMembers.teamId, teams.id))
      .where(eq(teamMembers.userId, userId));
    return rows;
  }),

  createTeam: contributorProcedure
    .input(
      z.object({
        name: z.string().trim().min(1).max(100),
        slug: z.string().trim().min(1).max(100).optional(),
        description: z.string().max(2000).optional(),
        orgType: orgTypeSchema.optional(),
        specialties: z.array(z.string().max(100)).max(50).optional(),
        website: z.string().url().optional(),
        serviceArea: z.record(z.unknown()).optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const requestedSlug = input.slug ? normalizeSlug(input.slug) : null;
      if (input.slug && !requestedSlug) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "That organization URL is not available",
        });
      }

      try {
        return await ctx.db.transaction(async (transaction) => {
          if (requestedSlug) {
            const [taken] = await transaction
              .select({ id: teams.id })
              .from(teams)
              .where(eq(teams.slug, requestedSlug))
              .limit(1);
            if (taken) {
              throw new TRPCError({
                code: "CONFLICT",
                message: "That organization URL is already taken",
              });
            }
          }
          const slug =
            requestedSlug ?? (await generateUniqueSlug(transaction, input.name));

          const [team] = await transaction
            .insert(teams)
            .values({
              name: input.name,
              slug,
              description: input.description,
              orgType: input.orgType,
              specialties: input.specialties ?? [],
              website: input.website,
              serviceArea: input.serviceArea ?? null,
              createdBy: userId,
            })
            .returning();

          await transaction.insert(teamMembers).values({
            teamId: team.id,
            userId,
            teamRole: "owner",
          });
          await adoptActiveTeam(transaction, userId, team.id);
          return team;
        });
      } catch (error) {
        if (error instanceof TRPCError) throw error;
        if (error instanceof SlugGenerationError) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "Could not derive a unique organization URL",
          });
        }
        if (isUniqueConstraintViolation(error)) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "That organization URL is already taken",
          });
        }
        throw error;
      }
    }),

  updateTeam: contributorProcedure
    .input(
      z.object({
        id: z.string().uuid(),
        name: z.string().trim().min(1).max(100).optional(),
        slug: z.string().trim().min(1).max(100).optional(),
        description: z.string().max(2000).optional(),
        orgType: orgTypeSchema.optional(),
        specialties: z.array(z.string().max(100)).max(50).optional(),
        website: z.string().url().optional().nullable(),
        serviceArea: z.record(z.unknown()).optional().nullable(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      const role = await requireTeamAccess(ctx.db, input.id, userId, {
        platformAdmin,
        allow: isTeamEditorRole,
        message: "Not authorized to update this organization",
      });

      const updates: Partial<typeof teams.$inferInsert> = {};
      if (input.name !== undefined) updates.name = input.name;
      if (input.description !== undefined) updates.description = input.description;
      if (input.orgType !== undefined) updates.orgType = input.orgType;
      if (input.specialties !== undefined) updates.specialties = input.specialties;
      if (input.website !== undefined) updates.website = input.website;
      if (input.serviceArea !== undefined) {
        updates.serviceArea = input.serviceArea ?? null;
      }

      if (input.slug !== undefined) {
        if (!platformAdmin && role !== "owner") {
          throw new TRPCError({
            code: "FORBIDDEN",
            message: "Only an owner can change the organization URL",
          });
        }
        const nextSlug = normalizeSlug(input.slug);
        if (!nextSlug) {
          throw new TRPCError({
            code: "BAD_REQUEST",
            message: "That organization URL is not available",
          });
        }
        const [taken] = await ctx.db
          .select({ id: teams.id })
          .from(teams)
          .where(eq(teams.slug, nextSlug))
          .limit(1);
        if (taken && taken.id !== input.id) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "That organization URL is already taken",
          });
        }
        updates.slug = nextSlug;
      }

      if (Object.keys(updates).length === 0) {
        const [current] = await ctx.db
          .select()
          .from(teams)
          .where(eq(teams.id, input.id))
          .limit(1);
        if (!current) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "Organization not found",
          });
        }
        return current;
      }

      try {
        const [updated] = await ctx.db
          .update(teams)
          .set(updates)
          .where(eq(teams.id, input.id))
          .returning();
        if (!updated) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "Organization not found",
          });
        }
        return updated;
      } catch (error) {
        if (error instanceof TRPCError) throw error;
        if (isUniqueConstraintViolation(error)) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "That organization URL is already taken",
          });
        }
        throw error;
      }
    }),

  setActiveTeam: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const membership = await loadMembership(ctx.db, input.teamId, userId);
      if (!membership.isMember) {
        throw new TRPCError({
          code: "FORBIDDEN",
          message: "Not a member of this organization",
        });
      }
      await ctx.db
        .update(users)
        .set({ activeTeamId: input.teamId })
        .where(eq(users.id, userId));
      return { teamId: input.teamId, teamRole: membership.role };
    }),
};
