import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSSE } from "@/hooks/useSSE";

class MockEventSource {
  static instances: MockEventSource[] = [];
  readonly listeners = new Map<string, (event: MessageEvent) => void>();
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, listener);
  }

  close() {
    this.closed = true;
  }
}

describe("useSSE live-only retry contract", () => {
  afterEach(() => {
    MockEventSource.instances = [];
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reconnects to the canonical stream URL without inventing a replay cursor", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", MockEventSource);
    const { unmount } = renderHook(() => useSSE("/api/stream/fire-detections"));

    const initial = MockEventSource.instances[0];
    act(() => {
      initial.listeners.get("feature")?.({
        data: JSON.stringify({ type: "Feature" }),
        lastEventId: "42",
      } as MessageEvent);
      initial.onerror?.();
      vi.advanceTimersByTime(1_000);
    });

    expect(initial.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1]?.url).toBe("/api/stream/fire-detections");
    unmount();
  });

  it("ignores a stale connection failure after a newer connection is active", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", MockEventSource);
    const { rerender, unmount } = renderHook(({ url }) => useSSE(url), {
      initialProps: { url: "/api/stream/first" },
    });

    const first = MockEventSource.instances[0];
    rerender({ url: "/api/stream/second" });
    const second = MockEventSource.instances[1];
    act(() => {
      first.onerror?.();
      vi.advanceTimersByTime(30_000);
    });

    expect(second.closed).toBe(false);
    expect(MockEventSource.instances).toHaveLength(2);
    unmount();
  });
});
