import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { RegionalIntelligenceResponse } from '@/lib/regional-intelligence';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
  parsedResponse?: RegionalIntelligenceResponse;
}

export type LocationPrecision = 'approximate' | 'exact';

interface SelectedLocation {
  lat: number;
  lon: number;
  precision: LocationPrecision;
}

interface RegionalIntelligenceState {
  isOpen: boolean;
  selectedLocation: SelectedLocation | null;
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  errorRetryable: boolean;
  analysisCancelled: boolean;
  dataFreshness: Record<string, string>;
  abortController: AbortController | null;
  conversationId: string | null;
  /** Human-readable note about what the agent is doing between text deltas. */
  toolActivity: string | null;

  openPanel: (lat: number, lon: number, precision: LocationPrecision) => void;
  closePanel: () => void;
  setLocation: (lat: number, lon: number, precision: LocationPrecision) => void;
  addMessage: (message: ChatMessage) => void;
  updateLastMessage: (partial: Partial<ChatMessage>) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null, retryable?: boolean) => void;
  setDataFreshness: (freshness: Record<string, string>) => void;
  setAbortController: (controller: AbortController | null) => void;
  cancelAnalysis: () => void;
  setAnalysisCancelled: (cancelled: boolean) => void;
  setConversationId: (id: string | null) => void;
  setToolActivity: (activity: string | null) => void;
}

export const useRegionalIntelligenceStore = create<RegionalIntelligenceState>()(
  devtools(
    (set, get) => ({
      isOpen: false,
      selectedLocation: null,
      messages: [],
      isLoading: false,
      error: null,
      errorRetryable: false,
      analysisCancelled: false,
      dataFreshness: {},
      abortController: null,
      conversationId: null,
      toolActivity: null,

      openPanel: (lat, lon, precision) => {
        get().abortController?.abort();
        set({
          isOpen: true,
          selectedLocation: { lat, lon, precision },
          messages: [],
          error: null,
          errorRetryable: false,
          analysisCancelled: false,
          dataFreshness: {},
          abortController: null,
          conversationId: null,
          toolActivity: null,
        });
      },

      closePanel: () => {
        get().abortController?.abort();
        set({
          isOpen: false,
          selectedLocation: null,
          messages: [],
          isLoading: false,
          error: null,
          errorRetryable: false,
          analysisCancelled: false,
          dataFreshness: {},
          abortController: null,
          conversationId: null,
          toolActivity: null,
        });
      },

      setLocation: (lat, lon, precision) => {
        get().abortController?.abort();
        set({
          selectedLocation: { lat, lon, precision },
          messages: [],
          error: null,
          errorRetryable: false,
          analysisCancelled: false,
          dataFreshness: {},
          abortController: null,
          conversationId: null,
          toolActivity: null,
        });
      },

      addMessage: (message) =>
        set((s) => ({ messages: [...s.messages, message] })),

      updateLastMessage: (partial) =>
        set((s) => {
          const messages = [...s.messages];
          if (messages.length > 0) {
            messages[messages.length - 1] = {
              ...messages[messages.length - 1],
              ...partial,
            };
          }
          return { messages };
        }),

      setLoading: (isLoading) => set({ isLoading }),
      setError: (error, errorRetryable = false) =>
        set({ error, errorRetryable: error === null ? false : errorRetryable }),
      setDataFreshness: (dataFreshness) => set({ dataFreshness }),
      setAbortController: (abortController) => set({ abortController }),
      cancelAnalysis: () => {
        get().abortController?.abort();
        set((state) => {
          const messages = [...state.messages];
          const lastMessage = messages.at(-1);
          if (lastMessage?.role === 'assistant' && lastMessage.isStreaming) {
            messages[messages.length - 1] = {
              ...lastMessage,
              content: 'Analysis canceled. No analysis was completed.',
              isStreaming: false,
            };
          }
          return {
            messages,
            isLoading: false,
            abortController: null,
            analysisCancelled: true,
            error: null,
            errorRetryable: false,
            toolActivity: null,
          };
        });
      },
      setAnalysisCancelled: (analysisCancelled) => set({ analysisCancelled }),
      setConversationId: (conversationId) => set({ conversationId }),
      setToolActivity: (toolActivity) => set({ toolActivity }),
    }),
    { name: 'regional-intelligence' }
  )
);
