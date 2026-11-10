import { randomBytes } from "crypto";
import { TRPCError } from "@trpc/server";
import { and, eq } from "drizzle-orm";
import { z } from "zod";
import { isPlanBbox } from "@/lib/plans/bbox";
import { normalizeRefCode, readPlanRefCookie } from "@/lib/plans/ref-code";
import { SAMPLE_PLAN_PUBLIC_ID } from "@/lib/plans/sample";
import { readAevaniCatalog } from "@/lib/server/aevani/catalog";
import { sitePlanItems, sitePlans } from "@/lib/server/db/schema";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import { canEditSitePlan, issueGuestPlanToken } from "@/lib/server/plans/access";
import { ipRateLimitKey } from "@/lib/server/security/registration";
import type { Context } from "@/lib/server/trpc/init";
import { publicProcedure, router } from "@/lib/server/trpc/init";

const PLAN_CREATES_PER_MINUTE = 10;
const PLAN_EDITS_PER_MINUTE = 60;

const publicIdSchema = z
  .string()
  .trim()
  .regex(/^(?:sample-ada-county|plan_[A-Za-z0-9_-]{16,64})$/);

const guestTokenSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9_-]{16,512}$/)
  .optional();

const itemFields = {
  id: sitePlanItems.id,
  aevaniProductId: sitePlanItems.aevaniProductId,
  catalogSlug: sitePlanItems.catalogSlug,
  title: sitePlanItems.title,
  quantity: sitePlanItems.quantity,
  notes: sitePlanItems.notes,
};

function sessionUserId(session: Context["session"]): string | null {
  const id = session?.user?.id;
  return typeof id === "string" && id.length > 0 ? id : null;
}

async function enforcePlanRateLimit(
  request: Request | undefined,
  bucket: string,
  limit: number
): Promise<void> {
  if (!request) {
    throw new TRPCError({
      code: "INTERNAL_SERVER_ERROR",
      message: "A rate-limited procedure needs its HTTP request",
    });
  }
  const rate = await checkRateLimit(ipRateLimitKey(request, bucket), limit);
  if (!rate.available) {
    throw new TRPCError({
      code: "SERVICE_UNAVAILABLE",
      message: "Rate limiting is temporarily unavailable",
    });
  }
  if (rate.limited) {
    throw new TRPCError({
      code: "TOO_MANY_REQUESTS",
      message: `Too many requests; retry after ${rate.retryAfter} s`,
    });
  }
}

async function listItems(db: Context["db"], planId: string) {
  return db
    .select(itemFields)
    .from(sitePlanItems)
    .where(eq(sitePlanItems.planId, planId))
    .orderBy(sitePlanItems.createdAt);
}

function presentPlan(
  plan: {
    publicId: string;
    title: string;
    description: string | null;
    isSample: boolean;
    sampleLabel: string | null;
    bbox: unknown;
    ownerUserId: string | null;
    guestTokenHash: string | null;
  },
  items: Awaited<ReturnType<typeof listItems>>,
  guestToken: string | undefined,
  currentUserId: string | null
) {
  return {
    publicId: plan.publicId,
    title: plan.title,
    description: plan.description,
    isSample: plan.isSample,
    sampleLabel: plan.sampleLabel,
    bbox: plan.bbox,
    savedToAccount: plan.ownerUserId !== null,
    canEdit: canEditSitePlan({
      isSample: plan.isSample,
      ownerUserId: plan.ownerUserId,
      sessionUserId: currentUserId,
      guestToken,
      guestTokenHash: plan.guestTokenHash,
    }),
    items,
  };
}

async function loadPlan(db: Context["db"], publicId: string) {
  const [plan] = await db
    .select()
    .from(sitePlans)
    .where(eq(sitePlans.publicId, publicId))
    .limit(1);
  return plan ?? null;
}

async function requireEditablePlan(
  ctx: Context,
  publicId: string,
  guestToken: string | undefined
) {
  const plan = await loadPlan(ctx.db, publicId);
  if (!plan) throw new TRPCError({ code: "NOT_FOUND" });
  if (
    !canEditSitePlan({
      isSample: plan.isSample,
      ownerUserId: plan.ownerUserId,
      sessionUserId: sessionUserId(ctx.session),
      guestToken,
      guestTokenHash: plan.guestTokenHash,
    })
  ) {
    throw new TRPCError({ code: "FORBIDDEN" });
  }
  return plan;
}

export const plansRouter = router({
  getSample: publicProcedure.query(async ({ ctx }) => {
    const plan = await loadPlan(ctx.db, SAMPLE_PLAN_PUBLIC_ID);
    if (!plan?.isSample) return null;
    const items = await listItems(ctx.db, plan.id);
    return presentPlan(plan, items, undefined, sessionUserId(ctx.session));
  }),

  getByPublicId: publicProcedure
    .input(z.object({ publicId: publicIdSchema, guestToken: guestTokenSchema }))
    .query(async ({ ctx, input }) => {
      const plan = await loadPlan(ctx.db, input.publicId);
      if (!plan) throw new TRPCError({ code: "NOT_FOUND" });
      const items = await listItems(ctx.db, plan.id);
      return presentPlan(plan, items, input.guestToken, sessionUserId(ctx.session));
    }),

  /**
   * A signed-in caller owns the new plan. Everyone else gets a guest token
   * returned once; only its hash is stored. The map area is copied from the
   * sample so this procedure does not accept a client bbox.
   */
  create: publicProcedure
    .input(
      z.object({
        title: z.string().trim().min(1).max(200).optional(),
        refCode: z.string().trim().max(64).optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      await enforcePlanRateLimit(ctx.req, "plans:create", PLAN_CREATES_PER_MINUTE);
      const ownerUserId = sessionUserId(ctx.session);
      const refCode =
        normalizeRefCode(input.refCode) ??
        readPlanRefCookie(ctx.req?.headers.get("cookie"));

      const [sample] = await ctx.db
        .select({ bbox: sitePlans.bbox, viewport: sitePlans.viewport })
        .from(sitePlans)
        .where(and(eq(sitePlans.publicId, SAMPLE_PLAN_PUBLIC_ID), eq(sitePlans.isSample, true)))
        .limit(1);
      if (!sample || !isPlanBbox(sample.bbox)) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message: "The sample plan is not available",
        });
      }

      const issued = ownerUserId ? null : issueGuestPlanToken();
      const [created] = await ctx.db
        .insert(sitePlans)
        .values({
          publicId: `plan_${randomBytes(16).toString("base64url")}`,
          title: input.title ?? "Site plan",
          isSample: false,
          bbox: sample.bbox,
          viewport: sample.viewport,
          refCode,
          ownerUserId,
          guestTokenHash: issued?.tokenHash ?? null,
        })
        .returning({ publicId: sitePlans.publicId });
      if (!created) {
        throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "The plan was not saved" });
      }
      return { publicId: created.publicId, guestToken: issued?.token ?? null };
    }),

  addItem: publicProcedure
    .input(
      z.object({
        publicId: publicIdSchema,
        guestToken: guestTokenSchema,
        aevaniProductId: z.string().trim().min(1).max(80),
        quantity: z.number().int().min(1).max(999).default(1),
        notes: z.string().trim().max(2_000).optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      await enforcePlanRateLimit(ctx.req, "plans:edit", PLAN_EDITS_PER_MINUTE);
      const plan = await requireEditablePlan(ctx, input.publicId, input.guestToken);
      const catalog = await readAevaniCatalog();
      const product =
        catalog.status === "ready"
          ? catalog.products.find((entry) => entry.id === input.aevaniProductId)
          : undefined;
      if (!product) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "That catalog item is not available",
        });
      }
      await ctx.db
        .insert(sitePlanItems)
        .values({
          planId: plan.id,
          aevaniProductId: product.id,
          title: product.title,
          quantity: input.quantity,
          notes: input.notes ? input.notes : null,
        })
        .onConflictDoNothing({
          target: [sitePlanItems.planId, sitePlanItems.aevaniProductId],
        });
      await ctx.db
        .update(sitePlans)
        .set({ updatedAt: new Date() })
        .where(eq(sitePlans.id, plan.id));
      return listItems(ctx.db, plan.id);
    }),

  updateItem: publicProcedure
    .input(
      z.object({
        publicId: publicIdSchema,
        guestToken: guestTokenSchema,
        itemId: z.string().uuid(),
        quantity: z.number().int().min(1).max(999),
        notes: z.string().trim().max(2_000).nullable(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      await enforcePlanRateLimit(ctx.req, "plans:edit", PLAN_EDITS_PER_MINUTE);
      const plan = await requireEditablePlan(ctx, input.publicId, input.guestToken);
      const [updated] = await ctx.db
        .update(sitePlanItems)
        .set({
          quantity: input.quantity,
          notes: input.notes && input.notes.length > 0 ? input.notes : null,
        })
        .where(and(eq(sitePlanItems.id, input.itemId), eq(sitePlanItems.planId, plan.id)))
        .returning({ id: sitePlanItems.id });
      if (!updated) throw new TRPCError({ code: "NOT_FOUND" });
      await ctx.db
        .update(sitePlans)
        .set({ updatedAt: new Date() })
        .where(eq(sitePlans.id, plan.id));
      return listItems(ctx.db, plan.id);
    }),

  removeItem: publicProcedure
    .input(
      z.object({
        publicId: publicIdSchema,
        guestToken: guestTokenSchema,
        itemId: z.string().uuid(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      await enforcePlanRateLimit(ctx.req, "plans:edit", PLAN_EDITS_PER_MINUTE);
      const plan = await requireEditablePlan(ctx, input.publicId, input.guestToken);
      const [removed] = await ctx.db
        .delete(sitePlanItems)
        .where(and(eq(sitePlanItems.id, input.itemId), eq(sitePlanItems.planId, plan.id)))
        .returning({ id: sitePlanItems.id });
      if (!removed) throw new TRPCError({ code: "NOT_FOUND" });
      await ctx.db
        .update(sitePlans)
        .set({ updatedAt: new Date() })
        .where(eq(sitePlans.id, plan.id));
      return listItems(ctx.db, plan.id);
    }),
});
