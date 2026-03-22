import { z } from 'zod';
import { protectedProcedure, router } from '../init';
import { aiConversations, aiMessages } from '@/lib/server/db/schema';
import { eq, and, desc, asc } from 'drizzle-orm';

export const regionalIntelligenceRouter = router({
  getConversations: protectedProcedure
    .input(z.object({ limit: z.number().min(1).max(100).default(20) }))
    .query(async ({ ctx, input }) => {
      const userId = (ctx.session!.user as { id: string }).id;
      return ctx.db
        .select()
        .from(aiConversations)
        .where(eq(aiConversations.userId, userId))
        .orderBy(desc(aiConversations.updatedAt))
        .limit(input.limit);
    }),

  getConversation: protectedProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = (ctx.session!.user as { id: string }).id;
      const [conv] = await ctx.db
        .select()
        .from(aiConversations)
        .where(
          and(
            eq(aiConversations.id, input.id),
            eq(aiConversations.userId, userId)
          )
        )
        .limit(1);
      if (!conv) return null;

      const messages = await ctx.db
        .select()
        .from(aiMessages)
        .where(eq(aiMessages.conversationId, input.id))
        .orderBy(asc(aiMessages.createdAt));

      return { ...conv, messages };
    }),

  getRateLimitStatus: protectedProcedure.query(async ({ ctx }) => {
    try {
      const { getRedis } = await import('@/lib/server/redis');
      const redis = getRedis();
      const userId = (ctx.session!.user as { id: string }).id;
      const key = `ai-ratelimit:${userId}`;
      const now = Date.now();
      await redis.zremrangebyscore(key, 0, now - 3_600_000);
      const count = await redis.zcard(key);
      return {
        remaining: Math.max(0, 20 - count),
        resetAt:
          count >= 20 ? new Date(now + 3_600_000).toISOString() : null,
      };
    } catch {
      return { remaining: 20, resetAt: null };
    }
  }),
});
