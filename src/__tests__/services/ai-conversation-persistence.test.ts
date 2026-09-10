import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ returning: vi.fn(), values: vi.fn(), update: vi.fn(), where: vi.fn(), owner: vi.fn(), history: vi.fn(), orderBy: vi.fn() }));
vi.mock('@/lib/server/db', () => ({ db: {
  insert: () => ({ values: mocks.values }),
  update: mocks.update,
  select: () => ({ from: () => ({ where: () => ({ limit: mocks.owner, orderBy: (...args: unknown[]) => { mocks.orderBy(...args); return { limit: mocks.history }; } }) }) }),
} }));
import { openConversation, recordExchange } from '@/lib/server/services/ai-conversations';

beforeEach(() => {
  vi.clearAllMocks();
  mocks.values.mockReturnValue({ returning: mocks.returning });
  mocks.update.mockReturnValue({ set: () => ({ where: mocks.where }) });
  mocks.where.mockResolvedValue(undefined);
});

it('returns the actual persisted assistant identity without changing the structured report', async () => {
  mocks.returning.mockResolvedValue([{ id: 'user-id', role: 'user' }, { id: 'assistant-id', role: 'assistant' }]);
  const report = { riskSummary: { headline: 'As recorded' }, dataFreshness: { drought: '2026-09-01' } };
  expect(await recordExchange({ conversationId: 'owned', question: 'Explain', answer: 'As recorded', structuredResponse: report })).toEqual({ assistantMessageId: 'assistant-id' });
  expect(mocks.values).toHaveBeenCalledWith([
    { conversationId: 'owned', role: 'user', content: 'Explain' },
    { conversationId: 'owned', role: 'assistant', content: 'As recorded', structuredResponse: report },
  ]);
});

it('does not manufacture a feedback identity when persistence fails', async () => {
  mocks.returning.mockRejectedValue(new Error('storage unavailable'));
  await expect(recordExchange({ conversationId: 'owned', question: 'Explain', answer: 'Answer', structuredResponse: {} })).rejects.toThrow('storage unavailable');
});

it('loads only the newest bounded owned turns and restores chronological user/assistant order', async () => {
  mocks.owner.mockResolvedValue([{ id: 'owned' }]);
  mocks.history.mockResolvedValue([
    { role: 'assistant', content: 'Answer', structuredResponse: null, createdAt: '2026-09-10T12:00:00Z' },
    { role: 'user', content: 'Question', structuredResponse: null, createdAt: '2026-09-10T12:00:00Z' },
  ]);
  const result = await openConversation({ userId: 'owner', conversationId: 'owned', lat: 44, lon: -118, geohash: '44_-118' });
  expect(mocks.history).toHaveBeenCalledWith(9);
  expect(mocks.orderBy.mock.calls[0]).toHaveLength(3);
  expect(result.history.map(turn => turn.role)).toEqual(['user', 'assistant']);
  expect(result.history[0].content).toBe('Question');
});
