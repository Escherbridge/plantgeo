import { describe, expect, it, vi } from 'vitest';
import { PgDialect } from 'drizzle-orm/pg-core';
import type { SQL } from 'drizzle-orm';
import type { Context } from '@/lib/server/trpc/init';

vi.mock('@/lib/server/db', () => ({ db: {} }));
vi.mock('@/lib/server/auth', () => ({ getServerSession: vi.fn() }));
vi.mock('@/lib/server/security/regional-intelligence-access', () => ({
  readRegionalIntelligenceUsage: vi.fn(), REGIONAL_INTELLIGENCE_SERVING_STATE: 'available',
}));

import { regionalIntelligenceRouter } from '@/lib/server/trpc/routers/regional-intelligence';
import { aiMessageFeedback } from '@/lib/server/db/schema';

const USER = '11111111-1111-4111-8111-111111111111';
const CONVERSATION = '22222222-2222-4222-8222-222222222222';
const MESSAGE = '33333333-3333-4333-8333-333333333333';
const input = { conversationId: CONVERSATION, messageId: MESSAGE, rating: 'helpful' as const };

function harness(selectResults: unknown[][] = [[{ id: MESSAGE }]]) {
  const conditions: SQL[] = [];
  const values = vi.fn();
  const upsert = vi.fn().mockResolvedValue(undefined);
  const remove = vi.fn().mockResolvedValue(undefined);
  const database = {
    select: vi.fn(() => {
      const rows = selectResults.shift() ?? [];
      const builder = {
        from: vi.fn(() => builder), innerJoin: vi.fn(() => builder),
        where: vi.fn((condition: SQL) => { conditions.push(condition); return builder; }),
        limit: vi.fn(async () => rows), orderBy: vi.fn(async () => rows),
        then: (resolve: (result: unknown[]) => unknown) => Promise.resolve(rows).then(resolve),
      };
      return builder;
    }),
    insert: vi.fn(() => ({ values: (value: unknown) => { values(value); return { onConflictDoUpdate: upsert }; } })),
    delete: vi.fn(() => ({ where: remove })),
  };
  const ctx = { db: database as unknown as Context['db'], session: {
    expires: '2099-01-01', user: { id: USER },
  } } as Context;
  return { database, conditions, values, upsert, remove, ctx, caller: regionalIntelligenceRouter.createCaller(ctx) };
}

describe('private assistant message feedback', () => {
  it('requires authentication before touching metadata', async () => {
    const h = harness();
    const caller = regionalIntelligenceRouter.createCaller({ ...h.ctx, session: null });
    await expect(caller.setMessageFeedback(input)).rejects.toMatchObject({ code: 'UNAUTHORIZED' });
    expect(h.database.select).not.toHaveBeenCalled();
  });

  it('pins authorization to message, conversation, current owner and assistant role', async () => {
    const h = harness();
    expect(await h.caller.setMessageFeedback({ ...input, reason: ' Useful explanation ' }))
      .toEqual({ rating: 'helpful', reason: 'Useful explanation' });
    const query = new PgDialect().sqlToQuery(h.conditions[0]);
    expect(query.params).toEqual([MESSAGE, CONVERSATION, USER, 'assistant']);
    expect(query.sql).toContain('"ai_conversations"."user_id"');
    expect(query.sql).toContain('"ai_messages"."role"');
    expect(h.values).toHaveBeenCalledWith({ messageId: MESSAGE, userId: USER, rating: 'helpful', reason: 'Useful explanation' });
    expect(h.upsert).toHaveBeenCalledWith(expect.objectContaining({ target: [aiMessageFeedback.messageId, aiMessageFeedback.userId] }));
  });

  it('does not mutate when the ownership-and-role lookup finds no eligible message', async () => {
    const h = harness([[]]);
    await expect(h.caller.setMessageFeedback(input)).rejects.toMatchObject({ code: 'NOT_FOUND' });
    await expect(h.caller.setMessageFeedback({ ...input, rating: null })).rejects.toMatchObject({ code: 'NOT_FOUND' });
    expect(h.database.insert).not.toHaveBeenCalled();
    expect(h.database.delete).not.toHaveBeenCalled();
  });

  it('uses the same conflict key on repeated ratings and clears only this user/message', async () => {
    const h = harness([[{ id: MESSAGE }], [{ id: MESSAGE }], [{ id: MESSAGE }], [{ id: MESSAGE }]]);
    await h.caller.setMessageFeedback(input);
    await h.caller.setMessageFeedback({ ...input, rating: 'not_helpful', reason: '' });
    expect(h.upsert).toHaveBeenLastCalledWith(expect.objectContaining({
      target: [aiMessageFeedback.messageId, aiMessageFeedback.userId],
      set: expect.objectContaining({ rating: 'not_helpful', reason: null }),
    }));
    expect(await h.caller.setMessageFeedback({ ...input, rating: null })).toBeNull();
    expect(await h.caller.setMessageFeedback({ ...input, rating: null })).toBeNull();
    expect(h.remove).toHaveBeenCalledTimes(2);
    expect(new PgDialect().sqlToQuery(h.remove.mock.calls[0][0]).params).toEqual([MESSAGE, USER]);
  });

  it('rejects oversized explanations before any lookup', async () => {
    const h = harness();
    await expect(h.caller.setMessageFeedback({ ...input, reason: 'x'.repeat(1001) })).rejects.toMatchObject({ code: 'BAD_REQUEST' });
    expect(h.database.select).not.toHaveBeenCalled();
  });

  it('returns feedback alongside saved messages, scoped to the owner and conversation', async () => {
    const h = harness([[{ id: CONVERSATION, userId: USER }], [{ id: MESSAGE, role: 'assistant' }],
      [{ messageId: MESSAGE, rating: 'helpful', reason: null }]]);
    expect(await h.caller.getConversation({ id: CONVERSATION })).toMatchObject({
      messages: [{ id: MESSAGE, feedback: { rating: 'helpful', reason: null } }],
    });
    expect(new PgDialect().sqlToQuery(h.conditions[2]).params).toEqual([CONVERSATION, USER]);
  });

  it('does not read messages or feedback for another owner’s conversation', async () => {
    const h = harness([[]]);
    expect(await h.caller.getConversation({ id: CONVERSATION })).toBeNull();
    expect(h.database.select).toHaveBeenCalledTimes(1);
  });
});
