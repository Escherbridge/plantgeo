'use client';

import { useCallback, useEffect, useRef } from 'react';
import { useRegionalIntelligenceStore } from '@/stores/regional-intelligence-store';
import { useViewedLayerDays } from '@/lib/map/layer-toggle-context';
import {
  findLayerCapability,
  isDayDescribed,
  isWithinCoverageGap,
  useTimeSliderStore,
} from '@/stores/time-slider-store';
import type { RegionalIntelligenceResponse } from '@/lib/regional-intelligence';
import type { SliderCapabilities } from '@/types/time-slider';
import type { LocationPrecision } from '@/stores/regional-intelligence-store';

const DEFAULT_QUESTION = 'Analyze this location';

/**
 * The most layer rows one request posts. Mirrors MAX_VIEWED_LAYERS in the route.
 *
 * The list is sliced here rather than left to the server's bound, because the server answers
 * an oversized array with a 400 and the user loses the whole analysis over a reporting detail.
 * The registry holds 20 toggles, so this can only ever bite a registry that grew past the
 * server's bound before both halves redeployed.
 */
const MAX_VIEWED_LAYERS_POSTED = 24;

/** One layer row's own day, as posted with the analysis request. */
interface ViewedLayerReport {
  layer: string;
  date: string;
  hasDataOnDate: boolean;
}

/**
 * Whether this layer's own coverage record affirms that day as published.
 *
 * False means "the client cannot affirm it", which is NOT the same claim as "the warehouse
 * published nothing" -- a toggle no warehouse stream backs at all (the SoilGrids raster, the
 * SSURGO proxy) can never be affirmed from here. The server treats this as a claim rather than
 * a fact and re-derives coverage from the same record server-side; nothing downstream may turn
 * a false here into a stated absence.
 *
 * The `isDayDescribed` gate is the client half of the same fix `coverageOnDay` carries
 * server-side, and it has to be here too or the two halves disagree in the one direction that
 * hurts. `coverageGaps` is capped at its newest entries, so on a day below the reported boundary
 * "not in the gap list" means nothing at all -- and this function was reading that silence as a
 * yes. The server would then answer its own (now correct) "coverage unknown" while the client's
 * posted claim said the day had data, and because `clientClaimContradicted` is only computed on
 * the published and not-published branches, the disagreement was flagged nowhere.
 */
function reportsDataOnDate(
  capabilities: SliderCapabilities | null,
  warehouseLayerName: string | null,
  date: string
): boolean {
  if (warehouseLayerName === null) return false;
  const capability = findLayerCapability(capabilities, warehouseLayerName);
  if (capability === null || capability.earliestObservedDate === null) return false;
  if (date < capability.earliestObservedDate) return false;
  if (!isDayDescribed(capability, date)) return false;
  return !isWithinCoverageGap(capability, date);
}

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
  const viewedLayerDays = useViewedLayerDays();
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  // Held in a ref and read at SEND time rather than closed over, for two reasons: the user can
  // scrub between opening the panel and asking a follow-up, so the answer must describe the map
  // as it is at the moment they ask; and `queryLocation` must stay referentially stable because
  // MapView carries it in a useCallback dependency list.
  const viewedLayersRef = useRef<ViewedLayerReport[]>([]);
  useEffect(() => {
    viewedLayersRef.current = viewedLayerDays
      .slice(0, MAX_VIEWED_LAYERS_POSTED)
      .map((viewed) => ({
        layer: viewed.layerId,
        date: viewed.date,
        hasDataOnDate: reportsDataOnDate(
          capabilities,
          viewed.warehouseLayerName,
          viewed.date
        ),
      }));
  }, [viewedLayerDays, capabilities]);

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

      // Omitted rather than sent empty when nothing is visible: an empty array and an absent
      // field mean the same thing to the server, and the absent field is the one an older
      // client sends, so the two paths stay identical instead of nearly identical.
      const viewedLayers = viewedLayersRef.current.length
        ? viewedLayersRef.current
        : undefined;

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
            viewedLayers,
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
