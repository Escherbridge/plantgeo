import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SyncOperation } from "@/lib/offline/sync-queue";

const dequeueMock = vi.fn();
const removeMock = vi.fn();
const markConflictedMock = vi.fn();
const getConflictsMock = vi.fn();
const resolveConflictOpMock = vi.fn();

vi.mock("@/lib/offline/sync-queue", () => ({
  dequeue: (...args: unknown[]) => dequeueMock(...args),
  remove: (...args: unknown[]) => removeMock(...args),
  markConflicted: (...args: unknown[]) => markConflictedMock(...args),
  getConflicts: (...args: unknown[]) => getConflictsMock(...args),
  resolveConflict: (...args: unknown[]) => resolveConflictOpMock(...args),
}));

const toastSuccessMock = vi.fn();
const toastErrorMock = vi.fn();

vi.mock("@/components/ui/toast", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccessMock(...args),
    error: (...args: unknown[]) => toastErrorMock(...args),
  },
}));

import { useOfflineSync } from "@/hooks/useOfflineSync";

function makeOp(overrides: Partial<SyncOperation> = {}): SyncOperation {
  return {
    id: "op-1",
    type: "create",
    resource: "vegetation",
    data: { id: "res-1", value: 1 },
    timestamp: Date.now(),
    updatedAt: Date.now(),
    ...overrides,
  };
}

function triggerOnlineSync() {
  act(() => {
    window.dispatchEvent(new Event("online"));
  });
}

describe("useOfflineSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dequeueMock.mockResolvedValue([]);
    getConflictsMock.mockResolvedValue([]);
    removeMock.mockResolvedValue(undefined);
    markConflictedMock.mockResolvedValue(undefined);
    resolveConflictOpMock.mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockReset();
  });

  it("tracks online/offline transitions from window events", async () => {
    const { result } = renderHook(() => useOfflineSync());
    // Let the mount-time queue/conflict reads settle before dispatching events, so their
    // state updates land inside act() instead of racing the assertions below.
    await act(async () => {
      await Promise.resolve();
    });

    act(() => window.dispatchEvent(new Event("offline")));
    expect(result.current.isOnline).toBe(false);

    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(result.current.isOnline).toBe(true));
  });

  it("drains the queue on reconnect and reports a sync-complete toast", async () => {
    const ops = [makeOp({ id: "a" }), makeOp({ id: "b" })];
    dequeueMock.mockResolvedValue(ops);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });

    const { result } = renderHook(() => useOfflineSync());
    triggerOnlineSync();

    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledTimes(1));

    expect(removeMock).toHaveBeenCalledWith("a");
    expect(removeMock).toHaveBeenCalledWith("b");
    expect(toastSuccessMock).toHaveBeenCalledWith(
      "Sync complete",
      expect.objectContaining({ description: "2 changes uploaded" })
    );
    await waitFor(() => expect(result.current.pendingCount).toBe(0));
    expect(toastErrorMock).not.toHaveBeenCalled();
  });

  it("routes a server 409 into the conflicts list instead of retrying it", async () => {
    const op = makeOp({ id: "conflicted" });
    dequeueMock.mockResolvedValue([op]);
    getConflictsMock
      .mockResolvedValueOnce([]) // initial mount refresh
      .mockResolvedValueOnce([{ ...op, conflictReason: "server-409", detectedAt: Date.now() }]);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({}),
    });

    const { result } = renderHook(() => useOfflineSync());
    triggerOnlineSync();

    await waitFor(() => expect(markConflictedMock).toHaveBeenCalledWith(op, "server-409"));
    await waitFor(() => expect(result.current.conflicts).toHaveLength(1));
    expect(removeMock).not.toHaveBeenCalled();
    expect(toastSuccessMock).not.toHaveBeenCalled();
  });

  it("routes a stale local snapshot into the conflicts list without submitting the write", async () => {
    const op = makeOp({ id: "stale", type: "update", updatedAt: 100 });
    dequeueMock.mockResolvedValue([op]);
    getConflictsMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ ...op, conflictReason: "stale-snapshot", detectedAt: Date.now() }]);
    // The pre-check GET reports a newer server updatedAt -- runSync must mark it conflicted
    // and never reach the write request.
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ updatedAt: 999 }),
    });

    const { result } = renderHook(() => useOfflineSync());
    triggerOnlineSync();

    await waitFor(() => expect(markConflictedMock).toHaveBeenCalledWith(op, "stale-snapshot"));
    await waitFor(() => expect(result.current.conflicts).toHaveLength(1));
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    // `isSnapshotStale` now goes through `createBudgetedFetch`, whose adapter always forwards
    // `init` positionally to the real fetch (`fetch(input, init)`) even when the call site
    // passed none -- so the recorded call carries an explicit `undefined` second argument where
    // a bare `fetch(url)` used to carry none. Same request, same URL, no init options; see
    // src/lib/net/request-budget.ts's `createBudgetedFetch`.
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/vegetation/res-1", undefined);
  });

  it("leaves a hard-failed op queued and reports a sync-failed toast", async () => {
    const op = makeOp({ id: "will-fail" });
    dequeueMock.mockResolvedValue([op]);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    const { result } = renderHook(() => useOfflineSync());
    triggerOnlineSync();

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledTimes(1));
    expect(toastErrorMock).toHaveBeenCalledWith(
      "Sync failed",
      expect.objectContaining({ description: "1 change failed" })
    );
    expect(removeMock).not.toHaveBeenCalled();
    expect(markConflictedMock).not.toHaveBeenCalled();
    expect(result.current.pendingCount).toBe(1);
  });

  it("resolveConflict delegates to the queue module and refreshes state", async () => {
    const { result } = renderHook(() => useOfflineSync());

    await act(async () => {
      await result.current.resolveConflict("op-1", "keep-local");
    });

    expect(resolveConflictOpMock).toHaveBeenCalledWith("op-1", "keep-local");
    expect(getConflictsMock).toHaveBeenCalled();
    expect(dequeueMock).toHaveBeenCalled();
  });
});
