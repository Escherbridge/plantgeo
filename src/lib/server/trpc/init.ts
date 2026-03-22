import { initTRPC, TRPCError } from "@trpc/server";
import superjson from "superjson";
import { db } from "@/lib/server/db";
import { getServerSession } from "@/lib/server/auth";

export const createTRPCContext = async () => {
  const session = await getServerSession();
  return { db, session };
};

export type Context = Awaited<ReturnType<typeof createTRPCContext>>;

const t = initTRPC.context<Context>().create({
  transformer: superjson,
});

export const router = t.router;
export const publicProcedure = t.procedure;

export const protectedProcedure = t.procedure.use(({ ctx, next }) => {
  if (!ctx.session?.user) {
    throw new TRPCError({ code: "UNAUTHORIZED" });
  }
  return next({ ctx: { ...ctx, session: ctx.session } });
});

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
