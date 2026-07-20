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
});
