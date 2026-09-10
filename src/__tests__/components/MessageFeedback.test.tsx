import { afterEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MessageFeedback } from '@/components/panels/MessageFeedback';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(), invalidate: vi.fn(),
  feedback: null as { rating: 'helpful' | 'not_helpful'; reason: string | null } | null,
  onSuccess: undefined as (() => Promise<void>) | undefined,
}));
vi.mock('@/lib/trpc/client', () => ({ trpc: {
  useUtils: () => ({ regionalIntelligence: { getConversation: { invalidate: mocks.invalidate } } }),
  regionalIntelligence: {
    getConversation: { useQuery: () => ({ data: { messages: [{ id: 'persisted', feedback: mocks.feedback }] }, isError: false, isLoading: false }) },
    setMessageFeedback: { useMutation: (options: { onSuccess: () => Promise<void> }) => {
      mocks.onSuccess = options.onSuccess;
      return { mutate: mocks.mutate, isPending: false };
    } },
  },
} }));
afterEach(() => { mocks.feedback = null; mocks.onSuccess = undefined; vi.clearAllMocks(); });

it('submits feedback for the exact persisted assistant message with an optional reason', () => {
  render(<MessageFeedback conversationId="conversation" messageId="persisted" />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Optional reason' }), { target: { value: '  Needs source context  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Not helpful' }));
  expect(mocks.mutate).toHaveBeenCalledWith({ conversationId: 'conversation', messageId: 'persisted', rating: 'not_helpful', reason: 'Needs source context' });
});
it('loads the saved rating and clears it explicitly when the selected rating is clicked', () => {
  mocks.feedback = { rating: 'helpful', reason: 'Useful evidence' };
  render(<MessageFeedback conversationId="conversation" messageId="persisted" />);
  const selected = screen.getByRole('button', { name: 'Helpful' });
  expect(selected.getAttribute('aria-pressed')).toBe('true');
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Useful evidence');
  fireEvent.click(selected);
  expect(mocks.mutate).toHaveBeenCalledWith({ conversationId: 'conversation', messageId: 'persisted', rating: null, reason: 'Useful evidence' });
});
it('preserves an unsaved reason across refreshed feedback and clears the draft after confirmed saving', async () => {
  mocks.feedback = { rating: 'helpful', reason: 'Saved reason' };
  const { rerender } = render(<MessageFeedback conversationId="conversation" messageId="persisted" />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Unsaved draft' } });
  mocks.feedback = { rating: 'helpful', reason: 'Refreshed reason' };
  rerender(<MessageFeedback conversationId="conversation" messageId="persisted" />);
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Unsaved draft');
  fireEvent.click(screen.getByRole('button', { name: 'Save reason' }));
  mocks.feedback = { rating: 'helpful', reason: 'Unsaved draft' };
  await act(async () => { await mocks.onSuccess?.(); });
  expect(mocks.invalidate).toHaveBeenCalledWith({ id: 'conversation' });
  mocks.feedback = { rating: 'helpful', reason: 'Later saved reason' };
  rerender(<MessageFeedback conversationId="conversation" messageId="persisted" />);
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Later saved reason');
});
