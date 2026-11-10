import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { submitAffiliateApplication } from "@/lib/server/affiliate/submit";
import { affiliateApplications } from "@/lib/server/db/schema";
import { publicProcedure, router } from "@/lib/server/trpc/init";

export const affiliateApplicationsRouter = router({
  /**
   * Same decision as POST /api/affiliate/apply. A tripped honeypot reports
   * success and writes nothing, matching the route's silent 204.
   */
  submit: publicProcedure
    .input(z.unknown())
    .mutation(async ({ ctx, input }) => {
      if (!ctx.req) {
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "A rate-limited procedure needs its HTTP request",
        });
      }
      const result = await submitAffiliateApplication(ctx.req, input, async (row) => {
        await ctx.db.insert(affiliateApplications).values(row);
      });
      if (result.status === 200 || result.status === 204) return { received: true as const };
      if (result.status === 429) {
        throw new TRPCError({
          code: "TOO_MANY_REQUESTS",
          message: `Too many requests; retry after ${result.retryAfter} s`,
        });
      }
      if (result.status === 503) {
        throw new TRPCError({
          code: "SERVICE_UNAVAILABLE",
          message: "Rate limiting is temporarily unavailable",
        });
      }
      throw new TRPCError({
        code: "BAD_REQUEST",
        message: "Check the name, email, and website, then try again.",
      });
    }),
});
