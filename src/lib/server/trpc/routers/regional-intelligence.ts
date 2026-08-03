import { z } from 'zod';
import { protectedProcedure, router } from '../init';
import { aiConversations, aiMessages } from '@/lib/server/db/schema';
import { eq, and, desc, asc } from 'drizzle-orm';
import {
  readRegionalIntelligenceUsage,
  REGIONAL_INTELLIGENCE_SERVING_STATE,
} from '@/lib/server/security/regional-intelligence-access';

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
    const userId = (ctx.session!.user as { id: string }).id;
    const usage = await readRegionalIntelligenceUsage(userId);
    return {
      state: REGIONAL_INTELLIGENCE_SERVING_STATE,
      tier: usage.tier,
      limit: usage.limit,
      remaining: usage.remaining,
      resetAt: usage.resetAt,
    };
  }),
});
