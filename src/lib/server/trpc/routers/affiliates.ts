import { TRPCError } from "@trpc/server";
import { z } from "zod";
import {
  AffiliateReviewError,
  approveAffiliateApplication,
  declineAffiliateApplication,
  listAffiliateApplications,
  loadAffiliateDashboard,
  type AffiliateReviewReason,
} from "@/lib/server/affiliate/review";
import { adminProcedure, protectedProcedure, router } from "@/lib/server/trpc/init";

function reviewError(error: AffiliateReviewError): TRPCError {
  const message: Record<AffiliateReviewReason, string> = {
    not_found: "That application was not found.",
    not_pending: "That application is no longer pending.",
    email_has_code: "This email already has an active code.",
    code_unavailable: "A code could not be issued. Try again.",
  };
  return new TRPCError({
    code: error.reason === "not_found" ? "NOT_FOUND" : "CONFLICT",
    message: message[error.reason],
  });
}

function sessionUserId(userId: string | undefined): string {
  if (!userId) throw new TRPCError({ code: "UNAUTHORIZED" });
  return userId;
}

export const affiliatesRouter = router({
  listApplications: adminProcedure.query(async ({ ctx }) => {
    return listAffiliateApplications(ctx.db);
  }),

  approve: adminProcedure
    .input(z.object({ applicationId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      try {
        return await approveAffiliateApplication(ctx.db, {
          applicationId: input.applicationId,
          reviewerId: sessionUserId(ctx.session?.user?.id),
        });
      } catch (error) {
        if (error instanceof AffiliateReviewError) throw reviewError(error);
        throw error;
      }
    }),

  decline: adminProcedure
    .input(z.object({ applicationId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      try {
        await declineAffiliateApplication(ctx.db, input.applicationId);
        return { declined: true as const };
      } catch (error) {
        if (error instanceof AffiliateReviewError) throw reviewError(error);
        throw error;
      }
    }),

  /** The signed-in affiliate's link and plans-started count, or null. */
  dashboard: protectedProcedure.query(async ({ ctx }) => {
    return loadAffiliateDashboard(ctx.db, ctx.session.user.id);
  }),
});
