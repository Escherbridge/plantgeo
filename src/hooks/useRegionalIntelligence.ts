'use client';

import { useCallback } from 'react';
import { useRegionalIntelligenceStore } from '@/stores/regional-intelligence-store';
import type { RegionalIntelligenceResponse } from '@/lib/regional-intelligence';
import type { LocationPrecision } from '@/stores/regional-intelligence-store';

const DEFAULT_QUESTION = 'Analyze this location';

class RegionalIntelligenceRequestError extends Error {
  constructor(
    message: string,
    readonly retryable: boolean
  ) {
    super(message);
    this.name = 'RegionalIntelligenceRequestError';
  }
}

function isRegionalIntelligenceResponse(
  value: unknown
): value is RegionalIntelligenceResponse {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  const riskSummary = record.riskSummary as { level?: unknown } | undefined;
  return (
    record.aiGenerated === true &&
    typeof riskSummary === 'object' &&
    riskSummary !== null &&
    ['low', 'moderate', 'high', 'critical'].includes(
      riskSummary.level as string
    ) &&
    Array.isArray(record.observations) &&
    Array.isArray(record.remediation)
  );
}

export function useRegionalIntelligence() {
  const store = useRegionalIntelligenceStore();

  const queryLocation = useCallback(
    async (
      lat: number,
      lon: number,
      question?: string,
      precision: LocationPrecision = 'approximate'
    ) => {
      const {
        setLoading,
        setError,
        addMessage,
        updateLastMessage,
        setDataFreshness,
        setAbortController,
        setAnalysisCancelled,
        setConversationId,
        setToolActivity,
        conversationId,
      } = useRegionalIntelligenceStore.getState();

      const controller = new AbortController();
      setAbortController(controller);
      setLoading(true);
      setError(null);
      setAnalysisCancelled(false);
      setToolActivity(null);

      const isCurrentRequest = () =>
        useRegionalIntelligenceStore.getState().abortController === controller;

      addMessage({
        id: crypto.randomUUID(),
        role: 'user',
        content: question ?? DEFAULT_QUESTION,
      });
      addMessage({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '',
        isStreaming: true,
      });

      try {
        // Prior turns live server-side under conversationId; the client never
        // supplies conversation history.
        const response = await fetch('/api/ai/regional-intelligence', {
          method: 'POST',
          headers: {
            Accept: 'text/event-stream',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            lat,
            lon,
            question,
            conversationId: conversationId ?? undefined,
            locationConsent: { precision, confirmed: true },
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const err = (await response
            .json()
            .catch(() => ({ error: 'Request failed' }))) as {
            error?: string;
            retryable?: boolean;
          };
          throw new RegionalIntelligenceRequestError(
            err.error ?? `HTTP ${response.status}`,
            typeof err.retryable === 'boolean'
              ? err.retryable
              : response.status === 429 || response.status >= 500
          );
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('No response body');

        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (!isCurrentRequest()) return;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim();
              continue;
            }
            if (!line.startsWith('data: ')) continue;

            let parsed: Record<string, unknown>;
            try {
              parsed = JSON.parse(line.slice(6)) as Record<string, unknown>;
            } catch {
              continue;
            }
            if (!isCurrentRequest()) return;

            switch (eventType) {
              case 'context':
                setDataFreshness(
                  (parsed.dataFreshness as Record<string, string>) ?? {}
                );
                if (typeof parsed.conversationId === 'string') {
                  setConversationId(parsed.conversationId);
                }
                break;
              case 'delta': {
                if (typeof parsed.text !== 'string') break;
                const current =
                  useRegionalIntelligenceStore.getState().messages;
                const last = current[current.length - 1];
                updateLastMessage({
                  content: (last?.content ?? '') + parsed.text,
                });
                break;
              }
              case 'search':
                setToolActivity(
                  `Searching the web: ${String(parsed.query ?? '').slice(0, 80)}`
                );
                break;
              case 'done':
                setToolActivity(null);
                if (isRegionalIntelligenceResponse(parsed)) {
                  updateLastMessage({
                    isStreaming: false,
                    parsedResponse: parsed,
                  });
                } else {
                  updateLastMessage({ isStreaming: false });
                  setError('The analysis came back in an unexpected shape.', true);
                }
                break;
              case 'error':
                setToolActivity(null);
                setError(
                  (parsed.message as string | undefined) ?? 'Unknown error',
                  parsed.retryable === true
                );
                updateLastMessage({ isStreaming: false });
                break;
            }
          }
        }
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return;
        if (isCurrentRequest()) {
          setError(
            err instanceof Error ? err.message : 'Unknown error',
            err instanceof RegionalIntelligenceRequestError
              ? err.retryable
              : true
          );
          updateLastMessage({ isStreaming: false });
        }
      } finally {
        if (isCurrentRequest()) {
          setLoading(false);
          setAbortController(null);
          setToolActivity(null);
        }
      }
    },
    []
  );

  const sendFollowUp = useCallback(
    async (question: string) => {
      const { selectedLocation } = useRegionalIntelligenceStore.getState();
      if (!selectedLocation) return;
      await queryLocation(
        selectedLocation.lat,
        selectedLocation.lon,
        question,
        selectedLocation.precision
      );
    },
    [queryLocation]
  );

  const retryLastRequest = useCallback(async () => {
    const state = useRegionalIntelligenceStore.getState();
    const location = state.selectedLocation;
    if (!location) return;
    const question = [...state.messages]
      .reverse()
      .find((message) => message.role === 'user')?.content;
    await queryLocation(
      location.lat,
      location.lon,
      question === DEFAULT_QUESTION ? undefined : question,
      location.precision
    );
  }, [queryLocation]);

  return {
    ...store,
    queryLocation,
    sendFollowUp,
    retryLastRequest,
  };
}
