import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { RegionalIntelligenceResponse } from '@/lib/server/services/ai-prompt';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
  parsedResponse?: RegionalIntelligenceResponse;
}

interface RegionalIntelligenceState {
  isOpen: boolean;
  selectedLocation: { lat: number; lon: number } | null;
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  dataFreshness: Record<string, string>;
  abortController: AbortController | null;
  conversationId: string | null;

  openPanel: (lat: number, lon: number) => void;
  closePanel: () => void;
  setLocation: (lat: number, lon: number) => void;
  addMessage: (message: ChatMessage) => void;
  updateLastMessage: (partial: Partial<ChatMessage>) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setDataFreshness: (freshness: Record<string, string>) => void;
  setAbortController: (controller: AbortController | null) => void;
  setConversationId: (id: string | null) => void;
}

export const useRegionalIntelligenceStore = create<RegionalIntelligenceState>()(
  devtools(
    (set, get) => ({
      isOpen: false,
      selectedLocation: null,
      messages: [],
      isLoading: false,
      error: null,
      dataFreshness: {},
      abortController: null,
      conversationId: null,

      openPanel: (lat, lon) => {
        get().abortController?.abort();
        set({
          isOpen: true,
          selectedLocation: { lat, lon },
          messages: [],
          error: null,
          dataFreshness: {},
          abortController: null,
          conversationId: null,
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
          dataFreshness: {},
          abortController: null,
          conversationId: null,
        });
      },

      setLocation: (lat, lon) => {
        get().abortController?.abort();
        set({
          selectedLocation: { lat, lon },
          messages: [],
          error: null,
          dataFreshness: {},
          abortController: null,
          conversationId: null,
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
      setError: (error) => set({ error }),
      setDataFreshness: (dataFreshness) => set({ dataFreshness }),
      setAbortController: (abortController) => set({ abortController }),
      setConversationId: (conversationId) => set({ conversationId }),
    }),
    { name: 'regional-intelligence' }
  )
);
