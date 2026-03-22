'use client';

import { useCallback } from 'react';
import { useRegionalIntelligenceStore } from '@/stores/regional-intelligence-store';
import type { ConversationTurn, RegionalIntelligenceResponse } from '@/lib/server/services/ai-prompt';

export function useRegionalIntelligence() {
  const store = useRegionalIntelligenceStore();

  const queryLocation = useCallback(
    async (lat: number, lon: number, question?: string) => {
      const {
        setLoading,
        setError,
        addMessage,
        updateLastMessage,
        setDataFreshness,
        setAbortController,
        messages,
      } = useRegionalIntelligenceStore.getState();

      const controller = new AbortController();
      setAbortController(controller);
      setLoading(true);
      setError(null);

      // Add user message
      const userMsg = question ?? 'Analyze this location';
      addMessage({ id: crypto.randomUUID(), role: 'user', content: userMsg });

      // Add placeholder assistant message
      addMessage({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '',
        isStreaming: true,
      });

      // Build history from messages before the two we just added
      const history: ConversationTurn[] = messages
        .filter((m) => !m.isStreaming)
        .map((m) => ({
          role: m.role,
          content: m.parsedResponse
            ? JSON.stringify(m.parsedResponse)
            : m.content,
        }));

      try {
        const response = await fetch('/api/ai/regional-intelligence', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lat, lon, question, history }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const err = await response
            .json()
            .catch(() => ({ error: 'Request failed' })) as { error?: string };
          throw new Error(err.error ?? `HTTP ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('No response body');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          let eventType = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7);
            } else if (line.startsWith('data: ')) {
              const data = line.slice(6);
              try {
                const parsed = JSON.parse(data) as Record<string, unknown>;

                switch (eventType) {
                  case 'context':
                    setDataFreshness(
                      (parsed.dataFreshness as Record<string, string>) ?? {}
                    );
                    break;
                  case 'delta': {
                    if (parsed.text) {
                      const current =
                        useRegionalIntelligenceStore.getState().messages;
                      const last = current[current.length - 1];
                      updateLastMessage({
                        content: ((last?.content ?? '') + parsed.text) as string,
                      });
                    }
                    break;
                  }
                  case 'done':
                    updateLastMessage({
                      isStreaming: false,
                      parsedResponse: parsed as unknown as RegionalIntelligenceResponse,
                    });
                    break;
                  case 'error':
                    setError(
                      (parsed.message as string | undefined) ?? 'Unknown error'
                    );
                    updateLastMessage({ isStreaming: false });
                    break;
                }
              } catch {
                // skip unparseable lines
              }
            }
          }
        }
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Unknown error');
        updateLastMessage({ isStreaming: false });
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const sendFollowUp = useCallback(
    async (question: string) => {
      const { selectedLocation } = useRegionalIntelligenceStore.getState();
      if (!selectedLocation) return;
      await queryLocation(selectedLocation.lat, selectedLocation.lon, question);
    },
    [queryLocation]
  );

  return {
    ...store,
    queryLocation,
    sendFollowUp,
  };
}
