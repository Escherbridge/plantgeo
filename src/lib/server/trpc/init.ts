import { initTRPC, TRPCError } from "@trpc/server";
import superjson from "superjson";
import { db } from "@/lib/server/db";
import { getServerSession } from "@/lib/server/auth";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";

/**
 * `req` is the HTTP request the fetch adapter served, absent for an in-process `createCaller`
 * (invitation/join routes, tests). Only a rate-limited procedure needs it; see
 * `publicRateLimit` below.
 */
export interface Context {
  db: typeof db;
  session: Awaited<ReturnType<typeof getServerSession>>;
  req?: Request;
}

/** The fetch adapter's `createContext`; it passes `{ req, resHeaders, info }`, of which only `req` is kept. */
export const createTRPCContext = async (options?: { req?: Request }): Promise<Context> => {
  const session = await getServerSession();
  return { db, session, req: options?.req };
};

const t = initTRPC.context<Context>().create({
  transformer: superjson,
});

export const router = t.router;
export const publicProcedure = t.procedure;

/**
 * Per-client fixed-window rate limit for a public procedure that spends a scarce upstream, the tRPC
 * twin of `enforcePublicProviderRateLimit` in the REST routes. 429 -> TOO_MANY_REQUESTS; the
 * limiter's own production fail-closed 503 (Redis down) -> SERVICE_UNAVAILABLE. A call with no HTTP
 * request has nothing to fingerprint and is refused rather than waved through.
 */
export function publicRateLimit(bucket: string, limit: number) {
  return t.middleware(async ({ ctx, next }) => {
    if (!ctx.req) {
      throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "A rate-limited procedure needs its HTTP request" });
    }
    const rate = await enforcePublicProviderRateLimit(ctx.req, bucket, limit);
    if (!rate.allowed) {
      throw new TRPCError({
        code: rate.status === 429 ? "TOO_MANY_REQUESTS" : "SERVICE_UNAVAILABLE",
        message:
          rate.status === 429
            ? `Too many requests; retry after ${rate.retryAfter} s`
            : "Rate limiting is temporarily unavailable",
      });
    }
    return next();
  });
}

export const protectedProcedure = t.procedure.use(({ ctx, next }) => {
  if (!ctx.session?.user) {
    throw new TRPCError({ code: "UNAUTHORIZED" });
  }
  return next({ ctx: { ...ctx, session: ctx.session } });
});

// Deliberately no org-scoped procedure: a session-carried `teamRole` invites
// callers to authorize on it. Organization routers re-read membership from the
// database inside the acting transaction instead.

export const contributorProcedure = t.procedure.use(({ ctx, next }) => {
  if (!ctx.session?.user) throw new TRPCError({ code: 'UNAUTHORIZED', message: 'Authentication required' });
  const role = (ctx.session?.user as { platformRole?: string } | undefined)?.platformRole;
  if (!role || !["contributor", "expert", "admin"].includes(role)) {
    throw new TRPCError({ code: "FORBIDDEN" });
  }
  return next({ ctx: { ...ctx, session: ctx.session! } });
});

export const expertProcedure = t.procedure.use(({ ctx, next }) => {
  const role = (ctx.session?.user as { platformRole?: string } | undefined)?.platformRole;
  if (!role || !["expert", "admin"].includes(role)) {
    throw new TRPCError({ code: "FORBIDDEN" });
  }
  return next({ ctx: { ...ctx, session: ctx.session! } });
});

export const adminProcedure = t.procedure.use(({ ctx, next }) => {
  const role = (ctx.session?.user as { platformRole?: string } | undefined)?.platformRole;
  if (role !== "admin") {
    throw new TRPCError({ code: "FORBIDDEN" });
  }
  return next({ ctx: { ...ctx, session: ctx.session! } });
});
