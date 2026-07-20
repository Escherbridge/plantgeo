'use client';

import { useCallback } from 'react';
import { useRegionalIntelligenceStore } from '@/stores/regional-intelligence-store';
import type {
  ConversationTurn,
  RegionalIntelligenceResponse,
} from '@/lib/regional-intelligence';
import type { LocationPrecision } from '@/stores/regional-intelligence-store';

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
  const riskSummary = record.riskSummary;
  return (
    typeof riskSummary === 'object' &&
    riskSummary !== null &&
    ['low', 'moderate', 'high', 'critical'].includes(
      (riskSummary as { level?: unknown }).level as string
    ) &&
    Array.isArray(record.historicalEvents) &&
    Array.isArray(record.actionableItems) &&
    Array.isArray(record.interventionRecommendations)
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
      } = useRegionalIntelligenceStore.getState();

      const controller = new AbortController();
      setAbortController(controller);
      setLoading(true);
      setError(null);
      setAnalysisCancelled(false);

      const isCurrentRequest = () =>
        useRegionalIntelligenceStore.getState().abortController === controller;

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

      // Build history from the current request state, excluding the two messages above.
      const history: ConversationTurn[] = useRegionalIntelligenceStore
        .getState()
        .messages
        .slice(0, -2)
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
          headers: {
            Accept: 'text/event-stream',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            lat,
            lon,
            question,
            history,
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

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (!isCurrentRequest()) return;

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
                    if (isCurrentRequest()) {
                      setDataFreshness(
                        (parsed.dataFreshness as Record<string, string>) ?? {}
                      );
                    }
                    break;
                  case 'delta': {
                    if (parsed.text) {
                      const current =
                        useRegionalIntelligenceStore.getState().messages;
                      const last = current[current.length - 1];
                      if (isCurrentRequest()) {
                        updateLastMessage({
                          content: ((last?.content ?? '') + parsed.text) as string,
                        });
                      }
                    }
                    break;
                  }
                  case 'done':
                    if (isCurrentRequest()) {
                      if (isRegionalIntelligenceResponse(parsed)) {
                        updateLastMessage({
                          isStreaming: false,
                          parsedResponse: parsed,
                        });
                      } else {
                        updateLastMessage({
                          isStreaming: false,
                          content:
                            typeof parsed.text === 'string'
                              ? parsed.text
                              : undefined,
                        });
                      }
                    }
                    break;
                  case 'error':
                    if (isCurrentRequest()) {
                      setError(
                        (parsed.message as string | undefined) ?? 'Unknown error',
                        parsed.retryable === true
                      );
                      updateLastMessage({ isStreaming: false });
                    }
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
    const question = [...state.messages]
      .reverse()
      .find((message) => message.role === "user")?.content;
    if (!location) return;
    await queryLocation(
      location.lat,
      location.lon,
      question === "Analyze this location" ? undefined : question,
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
