'use client';

import { useState } from 'react';
import { trpc } from '@/lib/trpc/client';

/** Private feedback tied to a persisted, owner-checked assistant message. */
export function MessageFeedback({ conversationId, messageId }: { conversationId: string; messageId: string }) {
  const utils = trpc.useUtils();
  const query = trpc.regionalIntelligence.getConversation.useQuery({ id: conversationId });
  const feedback = query.data?.messages.find(message => message.id === messageId)?.feedback;
  const [reasonDraft, setReasonDraft] = useState<string | null>(null);
  const reason = reasonDraft ?? feedback?.reason ?? '';
  const [status, setStatus] = useState('');
  const mutation = trpc.regionalIntelligence.setMessageFeedback.useMutation({
    onSuccess: async () => {
      await utils.regionalIntelligence.getConversation.invalidate({ id: conversationId });
      setReasonDraft(null);
      setStatus('Feedback saved privately.');
    },
    onError: () => setStatus('Feedback could not be saved. Please try again.'),
  });
  const save = (rating: 'helpful' | 'not_helpful' | null) => {
    setStatus('');
    mutation.mutate({ conversationId, messageId, rating, reason: reason.trim() || undefined });
  };
  if (query.isError) return <p className="text-xs text-gray-500">Feedback is temporarily unavailable.</p>;
  if (query.data === null) return <p className="text-xs text-gray-500">The saved conversation is unavailable.</p>;
  return <fieldset className="mt-3 space-y-2 rounded border p-2 text-xs" disabled={query.isLoading || mutation.isPending}>
    <legend className="px-1">Was this answer helpful?</legend>
    <div className="flex flex-wrap gap-2">
      <button type="button" aria-pressed={feedback?.rating === 'helpful'} onClick={() => save(feedback?.rating === 'helpful' ? null : 'helpful')} className="rounded border px-3 py-2 aria-pressed:bg-blue-100 dark:aria-pressed:bg-blue-900">Helpful</button>
      <button type="button" aria-pressed={feedback?.rating === 'not_helpful'} onClick={() => save(feedback?.rating === 'not_helpful' ? null : 'not_helpful')} className="rounded border px-3 py-2 aria-pressed:bg-blue-100 dark:aria-pressed:bg-blue-900">Not helpful</button>
    </div>
    <label className="block">Optional reason
      <textarea maxLength={1000} value={reason} onChange={event => setReasonDraft(event.target.value)} rows={2} className="mt-1 block w-full rounded border bg-transparent p-2" />
    </label>
    {feedback && <button type="button" onClick={() => save(feedback.rating)} className="rounded border px-3 py-2">Save reason</button>}
    <p className="text-gray-500">Private feedback on this saved answer. Click the selected rating again to clear it.</p>
    <p role="status">{mutation.isPending ? 'Saving feedback…' : status}</p>
  </fieldset>;
}
