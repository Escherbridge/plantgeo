import { z } from 'zod';
import { protectedProcedure, router } from '../init';
import { aiConversations, aiMessages, aiMessageFeedback } from '@/lib/server/db/schema';
import { TRPCError } from '@trpc/server';
import { eq, and, desc, asc, sql } from 'drizzle-orm';
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
        .orderBy(asc(aiMessages.createdAt), asc(sql`case when ${aiMessages.role} = 'user' then 0 else 1 end`), asc(aiMessages.id));

      const feedback = await ctx.db
        .select({ messageId: aiMessageFeedback.messageId, rating: aiMessageFeedback.rating, reason: aiMessageFeedback.reason })
        .from(aiMessageFeedback)
        .innerJoin(aiMessages, eq(aiMessageFeedback.messageId, aiMessages.id))
        .where(and(eq(aiMessages.conversationId, conv.id), eq(aiMessageFeedback.userId, userId)));
      const byMessage = new Map(feedback.map(({ messageId, rating, reason }) => [messageId, { rating, reason }]));
      return { ...conv, messages: messages.map(message => ({ ...message, feedback: byMessage.get(message.id) ?? null })) };
    }),

  /** Only the conversation owner can rate a persisted assistant message. */
  setMessageFeedback: protectedProcedure
    .input(z.object({
      conversationId: z.string().uuid(),
      messageId: z.string().uuid(),
      rating: z.enum(['helpful', 'not_helpful']).nullable(),
      reason: z.string().trim().max(1000).optional(),
    }))
    .mutation(async ({ ctx, input }) => {
      const userId = (ctx.session!.user as { id: string }).id;
      const [message] = await ctx.db
        .select({ id: aiMessages.id })
        .from(aiMessages)
        .innerJoin(aiConversations, eq(aiMessages.conversationId, aiConversations.id))
        .where(and(
          eq(aiMessages.id, input.messageId),
          eq(aiMessages.conversationId, input.conversationId),
          eq(aiConversations.userId, userId),
          eq(aiMessages.role, 'assistant'),
        ))
        .limit(1);
      if (!message) throw new TRPCError({ code: 'NOT_FOUND', message: 'Assistant message not found' });
      if (input.rating === null) {
        await ctx.db.delete(aiMessageFeedback).where(and(
          eq(aiMessageFeedback.messageId, message.id), eq(aiMessageFeedback.userId, userId),
        ));
        return null;
      }
      const feedback = { rating: input.rating, reason: input.reason || null };
      await ctx.db.insert(aiMessageFeedback)
        .values({ messageId: message.id, userId, ...feedback })
        .onConflictDoUpdate({
          target: [aiMessageFeedback.messageId, aiMessageFeedback.userId],
          set: { ...feedback, updatedAt: new Date() },
        });
      return feedback;
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
