import { z } from "zod";
import { router, publicProcedure, contributorProcedure } from "@/lib/server/trpc/init";

const bboxSchema = z.string().regex(/^-?\d+\.?\d*,-?\d+\.?\d*,-?\d+\.?\d*,-?\d+\.?\d*$/, 'Invalid bbox format: expected "west,south,east,north"');
import { strategyRequests, requestVotes, priorityZones } from "@/lib/server/db/schema";
import { eq, and, sql } from "drizzle-orm";

const STRATEGY_TYPES = [
  "keyline",
  "silvopasture",
  "reforestation",
  "biochar",
  "water_harvesting",
  "cover_cropping",
] as const;

export const communityRouter = router({
  submitRequest: contributorProcedure
    .input(
      z.object({
        strategyType: z.enum(STRATEGY_TYPES),
        title: z.string().min(3).max(200),
        description: z.string().optional(),
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      const [inserted] = await ctx.db
        .insert(strategyRequests)
        .values({
          userId: userId ?? null,
          strategyType: input.strategyType,
          title: input.title,
          description: input.description ?? null,
          lat: input.lat,
          lon: input.lon,
          status: "open",
          voteCount: 0,
        })
        .returning();
      return inserted;
    }),

  voteOnRequest: contributorProcedure
    .input(z.object({ requestId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session.user as { id?: string } | undefined)?.id;
      if (!userId) {
        throw new Error("User ID required to vote");
      }

      // Insert vote (will fail if already voted due to primary key constraint)
      const inserted = await ctx.db
        .insert(requestVotes)
        .values({
          requestId: input.requestId,
          userId,
        })
        .onConflictDoNothing()
        .returning();

      // Only increment if a row was actually inserted (not a duplicate vote)
      if (inserted.length > 0) {
        await ctx.db
          .update(strategyRequests)
          .set({ voteCount: sql`${strategyRequests.voteCount} + 1` })
          .where(eq(strategyRequests.id, input.requestId));
      }

      const [result] = await ctx.db
        .select({ voteCount: strategyRequests.voteCount })
        .from(strategyRequests)
        .where(eq(strategyRequests.id, input.requestId));

      return { voteCount: result?.voteCount ?? 0 };
    }),

  getRequests: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(), // "west,south,east,north"
        strategyType: z.string().optional(),
        limit: z.number().int().min(1).max(200).default(50),
      })
    )
    .query(async ({ ctx, input }) => {
      let query = ctx.db
        .select()
        .from(strategyRequests)
        .$dynamic();

      if (input.strategyType) {
        query = query.where(eq(strategyRequests.strategyType, input.strategyType));
      }

      if (input.bbox) {
        const parts = input.bbox.split(",").map(Number);
        if (parts.length === 4) {
          const [west, south, east, north] = parts;
          query = query.where(
            and(
              sql`${strategyRequests.lon} >= ${west}`,
              sql`${strategyRequests.lon} <= ${east}`,
              sql`${strategyRequests.lat} >= ${south}`,
              sql`${strategyRequests.lat} <= ${north}`
            )
          );
        }
      }

      const rows = await query
        .orderBy(sql`${strategyRequests.voteCount} DESC`)
        .limit(input.limit);

      return rows;
    }),

  getPriorityZones: publicProcedure
    .input(
      z.object({
        strategyType: z.string().optional(),
      })
    )
    .query(async ({ ctx, input }) => {
      if (input.strategyType) {
        return ctx.db
          .select()
          .from(priorityZones)
          .where(eq(priorityZones.strategyType, input.strategyType))
          .orderBy(sql`${priorityZones.totalVotes} DESC`);
      }
      return ctx.db
        .select()
        .from(priorityZones)
        .orderBy(sql`${priorityZones.totalVotes} DESC`);
    }),

  getRequestById: publicProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const result = await ctx.db
        .select()
        .from(strategyRequests)
        .where(eq(strategyRequests.id, input.id));
      return result[0] ?? null;
    }),
});
