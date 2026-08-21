import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { and, eq, inArray, isNull, or } from "drizzle-orm";
import {
  contributorProcedure,
  publicProcedure,
  router,
  type Context,
} from "@/lib/server/trpc/init";
import { layers, teamMembers } from "@/lib/server/db/schema";
import {
  clearLayerIdCache,
  invalidateLayerId,
} from "@/lib/server/services/environmental-read-model";
import {
  identityFromSession,
  isPlatformAdmin,
  isTeamEditorRole,
} from "@/lib/server/security/access-control";
import { layerVisibilityCondition } from "@/lib/server/security/layer-access";

const layerCreateSchema = z.object({
  name: z.string().min(1).max(100),
  type: z.string().default("vector"),
  description: z.string().optional(),
  style: z.record(z.unknown()).optional(),
  isPublic: z.boolean().optional(),
  minZoom: z.number().int().min(0).max(22).optional(),
  maxZoom: z.number().int().min(0).max(22).optional(),
  teamId: z.string().uuid().optional(),
  sortOrder: z.number().int().optional(),
});

const layerUpdateSchema = layerCreateSchema.partial().extend({
  id: z.string().uuid(),
});

function requireUserId(ctx: Context): string {
  const identity = identityFromSession(ctx.session);
  if (!identity.userId) {
    throw new TRPCError({ code: "UNAUTHORIZED" });
  }
  return identity.userId;
}

async function getTeamRole(
  ctx: Context,
  teamId: string,
  userId: string
): Promise<string | null> {
  const [membership] = await ctx.db
    .select({ role: teamMembers.teamRole })
    .from(teamMembers)
    .where(
      and(eq(teamMembers.teamId, teamId), eq(teamMembers.userId, userId))
    )
    .limit(1);
  return membership?.role ?? null;
}

async function assertCanMutateLayer(ctx: Context, id: string) {
  const [layer] = await ctx.db
    .select({ id: layers.id, teamId: layers.teamId })
    .from(layers)
    .where(eq(layers.id, id))
    .limit(1);
  if (!layer) throw new TRPCError({ code: "NOT_FOUND" });

  const identity = identityFromSession(ctx.session);
  if (isPlatformAdmin(identity)) return layer;
  if (!layer.teamId) {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: "Only administrators may mutate global layers",
    });
  }

  const role = await getTeamRole(ctx, layer.teamId, requireUserId(ctx));
  if (!isTeamEditorRole(role)) {
    throw new TRPCError({ code: "FORBIDDEN" });
  }
  return layer;
}

async function assertCanCreateInTeam(ctx: Context, teamId?: string) {
  const identity = identityFromSession(ctx.session);
  if (isPlatformAdmin(identity)) return;
  if (!teamId) {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: "Contributor layers must belong to a team",
    });
  }
  const role = await getTeamRole(ctx, teamId, requireUserId(ctx));
  if (!isTeamEditorRole(role)) {
    throw new TRPCError({ code: "FORBIDDEN" });
  }
}

async function assertCanSetLayerVisibility(ctx: Context, teamId?: string | null) {
  const identity = identityFromSession(ctx.session);
  if (isPlatformAdmin(identity)) return;
  if (!teamId) throw new TRPCError({ code: "FORBIDDEN" });
  const role = await getTeamRole(ctx, teamId, requireUserId(ctx));
  if (role !== "owner") {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: "Only team owners may change layer visibility",
    });
  }
}

export const layersRouter = router({
  list: publicProcedure
    .input(z.object({ teamId: z.string().uuid().optional() }).optional())
    .query(async ({ ctx, input }) => {
      const scope = input?.teamId
        ? or(eq(layers.teamId, input.teamId), isNull(layers.teamId))
        : isNull(layers.teamId);
      return ctx.db
        .select()
        .from(layers)
        .where(
          and(scope, layerVisibilityCondition(identityFromSession(ctx.session)))
        );
    }),

  getById: publicProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const [layer] = await ctx.db
        .select()
        .from(layers)
        .where(
          and(
            eq(layers.id, input.id),
            layerVisibilityCondition(identityFromSession(ctx.session))
          )
        )
        .limit(1);
      return layer ?? null;
    }),

  create: contributorProcedure
    .input(layerCreateSchema)
    .mutation(async ({ ctx, input }) => {
      await assertCanCreateInTeam(ctx, input.teamId);
      if (input.isPublic === true) {
        await assertCanSetLayerVisibility(ctx, input.teamId);
      }
      const [layer] = await ctx.db.insert(layers).values(input).returning();
      return layer;
    }),

  update: contributorProcedure
    .input(layerUpdateSchema)
    .mutation(async ({ ctx, input }) => {
      const existing = await assertCanMutateLayer(ctx, input.id);
      if (input.teamId && input.teamId !== existing.teamId) {
        await assertCanSetLayerVisibility(ctx, existing.teamId);
        await assertCanSetLayerVisibility(ctx, input.teamId);
      }
      if (input.isPublic !== undefined) {
        await assertCanSetLayerVisibility(
          ctx,
          input.teamId ?? existing.teamId
        );
      }
      const { id, ...data } = input;
      const [layer] = await ctx.db
        .update(layers)
        .set({ ...data, updatedAt: new Date() })
        .where(eq(layers.id, id))
        .returning();
      // A rename strands the previous name in the reader cache, and it is not in scope here.
      if (input.name !== undefined) {
        clearLayerIdCache();
      }
      return layer;
    }),

  delete: contributorProcedure
    .input(z.object({ id: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      await assertCanMutateLayer(ctx, input.id);
      const [deleted] = await ctx.db
        .delete(layers)
        .where(eq(layers.id, input.id))
        .returning({ name: layers.name });
      // Readers cache name -> layer_id; a delete must not leave the old id resolvable.
      if (deleted) {
        invalidateLayerId(deleted.name);
      }
      return { success: true };
    }),

  reorder: contributorProcedure
    .input(
      z.object({
        ids: z
          .array(z.string().uuid())
          .min(1)
          .max(100)
          .refine((ids) => new Set(ids).size === ids.length, {
            message: "Layer IDs must be unique",
          }),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const identity = identityFromSession(ctx.session);
      const rows = await ctx.db
        .select({ id: layers.id, teamId: layers.teamId })
        .from(layers)
        .where(inArray(layers.id, input.ids));
      if (rows.length !== input.ids.length) {
        throw new TRPCError({ code: "NOT_FOUND" });
      }

      if (!isPlatformAdmin(identity)) {
        const userId = requireUserId(ctx);
        const teamIds = [...new Set(rows.map((row) => row.teamId))];
        if (teamIds.some((teamId) => teamId === null)) {
          throw new TRPCError({ code: "FORBIDDEN" });
        }
        const memberships = await ctx.db
          .select({ teamId: teamMembers.teamId, role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.userId, userId),
              inArray(teamMembers.teamId, teamIds as string[])
            )
          );
        const editableTeams = new Set(
          memberships
            .filter((membership) => isTeamEditorRole(membership.role))
            .map((membership) => membership.teamId)
        );
        if (teamIds.some((teamId) => !teamId || !editableTeams.has(teamId))) {
          throw new TRPCError({ code: "FORBIDDEN" });
        }
      }

      await Promise.all(
        input.ids.map((id, index) =>
          ctx.db
            .update(layers)
            .set({ sortOrder: index, updatedAt: new Date() })
            .where(eq(layers.id, id))
        )
      );
      return { success: true };
    }),

  /**
   * Reports the bounding box scheduled ingestion is actually confined to.
   * The map renders globally, so without this the absence of data outside the
   * ingested region is indistinguishable from a genuine absence of phenomena.
   * Returns configured: false when INGEST_BBOX is unset, in which case no
   * ingestion job writes anything at all.
   */
  getIngestionCoverage: publicProcedure.query(() => {
    const raw = process.env.INGEST_BBOX?.trim();
    if (!raw) {
      return { configured: false as const, bbox: null };
    }
    const parts = raw.split(",").map(Number);
    if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) {
      return { configured: false as const, bbox: null };
    }
    const [west, south, east, north] = parts;
    return {
      configured: true as const,
      bbox: { west, south, east, north },
    };
  }),
});
