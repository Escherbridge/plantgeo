import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { RegionalAnalysisEvidence, RegionalIntelligenceResponse } from '@/lib/regional-intelligence';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
  parsedResponse?: RegionalIntelligenceResponse;
  savedMessageId?: string;
  createdAt?: string;
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
  analysisEvidence: RegionalAnalysisEvidence | null;
  abortController: AbortController | null;
  conversationId: string | null;
  /** Human-readable note about what the agent is doing between text deltas. */
  toolActivity: string | null;
  activity: { id: string; at: string; label: string }[];

  openPanel: (lat: number, lon: number, precision: LocationPrecision) => void;
  closePanel: () => void;
  setLocation: (lat: number, lon: number, precision: LocationPrecision) => void;
  addMessage: (message: ChatMessage) => void;
  updateLastMessage: (partial: Partial<ChatMessage>) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null, retryable?: boolean) => void;
  setDataFreshness: (freshness: Record<string, string>) => void;
  setAnalysisEvidence: (evidence: RegionalAnalysisEvidence | null) => void;
  setAbortController: (controller: AbortController | null) => void;
  cancelAnalysis: () => void;
  setAnalysisCancelled: (cancelled: boolean) => void;
  setConversationId: (id: string | null) => void;
  setToolActivity: (activity: string | null) => void;
  addActivity: (label: string) => void;
  resumeConversation: (conversation: { id: string; lat: number; lon: number; messages: ChatMessage[] }) => void;
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
      analysisEvidence: null,
      abortController: null,
      conversationId: null,
      toolActivity: null,
      activity: [],

      openPanel: (lat, lon, precision) => {
        get().abortController?.abort();
        set({
          isOpen: true,
          isLoading: false,
          selectedLocation: { lat, lon, precision },
          messages: [],
          error: null,
          errorRetryable: false,
          analysisCancelled: false,
          dataFreshness: {},
          analysisEvidence: null,
          abortController: null,
          conversationId: null,
          toolActivity: null,
          activity: [],
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
          analysisEvidence: null,
          abortController: null,
          conversationId: null,
          toolActivity: null,
          activity: [],
        });
      },

      setLocation: (lat, lon, precision) => {
        get().abortController?.abort();
        set({
          isLoading: false,
          selectedLocation: { lat, lon, precision },
          messages: [],
          error: null,
          errorRetryable: false,
          analysisCancelled: false,
          dataFreshness: {},
          analysisEvidence: null,
          abortController: null,
          conversationId: null,
          toolActivity: null,
          activity: [],
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
      setAnalysisEvidence: (analysisEvidence) => set({ analysisEvidence }),
      setAbortController: (abortController) => set({ abortController }),
      cancelAnalysis: () => {
        const wasActive = get().isLoading || get().abortController !== null;
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
        if (wasActive) get().addActivity('Analysis canceled.');
      },
      setAnalysisCancelled: (analysisCancelled) => set({ analysisCancelled }),
      setConversationId: (conversationId) => set({ conversationId }),
      setToolActivity: (toolActivity) => set({ toolActivity }),
      addActivity: (label) => set((state) => ({ activity: [...state.activity, { id: crypto.randomUUID(), at: new Date().toISOString(), label }].slice(-40) })),
      resumeConversation: (conversation) => {
        get().abortController?.abort();
        set({ isOpen: true, selectedLocation: { lat: conversation.lat, lon: conversation.lon, precision: 'approximate' },
          conversationId: conversation.id, messages: conversation.messages, isLoading: false,
          abortController: null, error: null, errorRetryable: false, analysisCancelled: false,
          dataFreshness: {}, analysisEvidence: null, toolActivity: null, activity: [],
        });
        get().addActivity('Saved conversation opened. No new analysis has been requested.');
      },
    }),
    { name: 'regional-intelligence' }
  )
);
