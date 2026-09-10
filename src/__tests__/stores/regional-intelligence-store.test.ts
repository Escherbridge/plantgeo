import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";

describe("regional intelligence request state", () => {
  beforeEach(() => {
    useRegionalIntelligenceStore.getState().closePanel();
  });

  it("finalizes a streaming response and exposes a resumable canceled state", () => {
    const abort = vi.fn();
    const store = useRegionalIntelligenceStore.getState();
    store.openPanel(40.12, -105.25, "approximate");
    store.addMessage({ id: "question", role: "user", content: "Analyze this location" });
    store.addMessage({ id: "pending", role: "assistant", content: "", isStreaming: true });
    store.setAbortController({ abort } as unknown as AbortController);
    store.setLoading(true);

    store.cancelAnalysis();

    const state = useRegionalIntelligenceStore.getState();
    expect(abort).toHaveBeenCalledOnce();
    expect(state.isLoading).toBe(false);
    expect(state.analysisCancelled).toBe(true);
    expect(state.messages.at(-1)).toMatchObject({
      content: "Analysis canceled. No analysis was completed.",
      isStreaming: false,
    });
  });
  it('resumes the saved conversation without starting a request or losing its persisted identity', () => {
    const abort = vi.fn();
    const store = useRegionalIntelligenceStore.getState();
    store.setAbortController({ abort } as unknown as AbortController);
    store.setLoading(true);
    store.resumeConversation({ id: 'owned-chat', lat: 44.66, lon: -118.83, messages: [{ id: 'message', savedMessageId: 'message', role: 'assistant', content: 'Saved historical answer' }] });
    const resumed = useRegionalIntelligenceStore.getState();
    expect(abort).toHaveBeenCalledOnce();
    expect(resumed).toMatchObject({ isOpen: true, isLoading: false, conversationId: 'owned-chat', abortController: null });
    expect(resumed.messages[0]).toMatchObject({ savedMessageId: 'message', content: 'Saved historical answer' });
    expect(resumed.activity[0].label).toContain('No new analysis has been requested');
    resumed.openPanel(44.66, -118.83, 'approximate');
    expect(useRegionalIntelligenceStore.getState()).toMatchObject({ conversationId: null, messages: [], activity: [], isLoading: false });
  });
});
