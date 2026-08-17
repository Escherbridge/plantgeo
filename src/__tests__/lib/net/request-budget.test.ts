import { readFileSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BURST_CAPACITY,
  MAX_CONCURRENT_REQUESTS,
  SUSTAINED_REQUESTS_PER_SECOND,
  acquireRequestSlot,
  createBudgetedFetch,
  getActiveRequestCount,
  getQueuedRequestCount,
  getQueuedRequestCountForLane,
  getRequestBudgetSnapshot,
  resetRequestBudgetForTests,
  runBudgeted,
  subscribeToRequestBudget,
  type RequestBudgetSnapshot,
} from "@/lib/net/request-budget";

/** A promise plus its own resolve/reject, so a test can decide exactly when a task finishes. */
function createDeferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  resetRequestBudgetForTests();
  vi.useRealTimers();
});

describe("documented constants", () => {
  it("locks in the chosen concurrency cap, sustained rate, and burst capacity", () => {
    expect(MAX_CONCURRENT_REQUESTS).toBe(4);
    expect(SUSTAINED_REQUESTS_PER_SECOND).toBe(5);
    expect(BURST_CAPACITY).toBe(8);
  });
});

describe("concurrency", () => {
  it("never lets active requests exceed maxConcurrent, even while more keep arriving", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 3, ratePerSecond: 1000, burstCapacity: 1000 });
    let maxObservedActive = 0;
    const deferreds = Array.from({ length: 10 }, () => createDeferred<void>());

    const results = deferreds.map((deferred) =>
      runBudgeted("lane", async () => {
        maxObservedActive = Math.max(maxObservedActive, getActiveRequestCount());
        await deferred.promise;
      })
    );

    await vi.advanceTimersByTimeAsync(0);
    expect(getActiveRequestCount()).toBe(3);
    expect(maxObservedActive).toBe(3);

    for (const deferred of deferreds) {
      deferred.resolve();
       
      await vi.advanceTimersByTimeAsync(0);
      expect(getActiveRequestCount()).toBeLessThanOrEqual(3);
      expect(maxObservedActive).toBeLessThanOrEqual(3);
    }

    await Promise.all(results);
    expect(getActiveRequestCount()).toBe(0);
  });
});

describe("rate limiting", () => {
  it("allows a burst instantly, then holds new dispatches to the sustained rate", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1000, ratePerSecond: 2, burstCapacity: 3 });
    const startedAt = Date.now();
    const dispatchTimes: number[] = [];

    for (let index = 0; index < 5; index += 1) {
      void runBudgeted("lane", async () => {
        dispatchTimes.push(Date.now());
      });
    }

    // The full burst clears immediately -- nothing here should wait on a timer.
    await vi.advanceTimersByTimeAsync(0);
    expect(dispatchTimes).toEqual([startedAt, startedAt, startedAt]);

    // At 2/s, the 4th dispatch needs a full 500ms even though concurrency has room to spare.
    await vi.advanceTimersByTimeAsync(400);
    expect(dispatchTimes.length).toBe(3);
    await vi.advanceTimersByTimeAsync(100);
    expect(dispatchTimes.length).toBe(4);
    expect(dispatchTimes[3]).toBe(startedAt + 500);

    // ...and the 5th needs another full 500ms after that -- the rate holds, it does not average out.
    await vi.advanceTimersByTimeAsync(499);
    expect(dispatchTimes.length).toBe(4);
    await vi.advanceTimersByTimeAsync(1);
    expect(dispatchTimes.length).toBe(5);
    expect(dispatchTimes[4]).toBe(startedAt + 1000);
  });
});

describe("cancellation", () => {
  it("rejects immediately, without ever consuming a slot, when the signal is already aborted", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const controller = new AbortController();
    controller.abort();

    await expect(acquireRequestSlot("lane", { signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(getActiveRequestCount()).toBe(0);
    expect(getQueuedRequestCount()).toBe(0);
  });

  it("drops a still-queued request the instant it aborts, without ever running its task", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const blocker = createDeferred<void>();
    void runBudgeted("lane", () => blocker.promise);
    await vi.advanceTimersByTimeAsync(0);
    expect(getActiveRequestCount()).toBe(1); // the sole slot is held by the blocker

    const queuedTask = vi.fn().mockResolvedValue(undefined);
    const controller = new AbortController();
    const queued = runBudgeted("lane", queuedTask, { signal: controller.signal });
    await vi.advanceTimersByTimeAsync(0);
    expect(getQueuedRequestCount()).toBe(1);

    controller.abort();
    await expect(queued).rejects.toMatchObject({ name: "AbortError" });
    expect(queuedTask).not.toHaveBeenCalled();
    expect(getQueuedRequestCount()).toBe(0);

    blocker.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(getActiveRequestCount()).toBe(0);
  });

  it("releases an active slot automatically on abort, and a caller's own release() afterward is a safe no-op", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const controller = new AbortController();

    const slotPromise = acquireRequestSlot("lane", { signal: controller.signal });
    await vi.advanceTimersByTimeAsync(0);
    expect(getActiveRequestCount()).toBe(1);

    controller.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(getActiveRequestCount()).toBe(0);

    const slot = await slotPromise;
    expect(() => slot.release()).not.toThrow();
    expect(getActiveRequestCount()).toBe(0);
  });
});

describe("fairness across lanes", () => {
  it("serves a late-arriving lane within one extra turn, never behind an entire 9-request burst", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const dispatchOrder: string[] = [];
    const deferreds = new Map<string, ReturnType<typeof createDeferred<void>>>();

    function launch(lane: string, id: string): Promise<void> {
      const deferred = createDeferred<void>();
      deferreds.set(id, deferred);
      return runBudgeted(lane, async () => {
        dispatchOrder.push(id);
        await deferred.promise;
      });
    }

    const results: Promise<void>[] = [];
    for (let index = 1; index <= 9; index += 1) {
      results.push(launch("climate", `climate${index}`));
    }
    // Arrives after the whole climate burst is already queued, exactly the scenario named in
    // the brief: a burst of 9 climate signals must not starve the one drought request behind it.
    results.push(launch("drought", "drought1"));

    await vi.advanceTimersByTimeAsync(0);
    expect(dispatchOrder).toEqual(["climate1"]);

    deferreds.get("climate1")!.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(dispatchOrder).toEqual(["climate1", "climate2"]);

    deferreds.get("climate2")!.resolve();
    await vi.advanceTimersByTimeAsync(0);
    // Round-robin, not FIFO: drought is served on its very next turn -- 3rd overall -- rather
    // than waiting for climate3..climate9 to drain first.
    expect(dispatchOrder).toEqual(["climate1", "climate2", "drought1"]);

    const remaining = [
      "drought1",
      "climate3",
      "climate4",
      "climate5",
      "climate6",
      "climate7",
      "climate8",
    ];
    for (const id of remaining) {
      deferreds.get(id)!.resolve();
       
      await vi.advanceTimersByTimeAsync(0);
    }
    deferreds.get("climate9")!.resolve();
    await vi.advanceTimersByTimeAsync(0);

    await Promise.all(results);
    expect(dispatchOrder).toEqual([
      "climate1",
      "climate2",
      "drought1",
      "climate3",
      "climate4",
      "climate5",
      "climate6",
      "climate7",
      "climate8",
      "climate9",
    ]);
  });
});

describe("createBudgetedFetch", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("passes input and init straight through to the real fetch once dispatched", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 5, ratePerSecond: 1000, burstCapacity: 1000 });
    const fetchMock = vi.fn().mockResolvedValue("mock-response" as unknown as Response);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const budgetedFetch = createBudgetedFetch("test-lane");
    const init: RequestInit = { headers: { "X-Test": "1" } };
    const response = await budgetedFetch("https://example.test/resource", init);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("https://example.test/resource", init);
    expect(response).toBe("mock-response");
  });

  it("gates dispatch behind the shared budget, same as any other lane", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const first = createDeferred<Response>();
    const second = createDeferred<Response>();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const budgetedFetch = createBudgetedFetch("gate-lane");
    const firstCall = budgetedFetch("https://example.test/a");
    const secondCall = budgetedFetch("https://example.test/b");

    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    first.resolve("a" as unknown as Response);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    second.resolve("b" as unknown as Response);
    await Promise.all([firstCall, secondCall]);
  });

  it("never calls the real fetch for a request that aborts while still queued", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const blocker = createDeferred<Response>();
    const fetchMock = vi.fn().mockReturnValue(blocker.promise);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const budgetedFetch = createBudgetedFetch("abort-lane");
    void budgetedFetch("https://example.test/blocker");
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const controller = new AbortController();
    const queued = budgetedFetch("https://example.test/queued", { signal: controller.signal });
    controller.abort();

    await expect(queued).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    blocker.resolve("done" as unknown as Response);
    await vi.advanceTimersByTimeAsync(0);
  });
});

describe("observability", () => {
  it("reports active/queued counts by lane and notifies subscribers as state changes", async () => {
    resetRequestBudgetForTests({ maxConcurrent: 1, ratePerSecond: 1000, burstCapacity: 1000 });
    const snapshots: RequestBudgetSnapshot[] = [];
    const unsubscribe = subscribeToRequestBudget((snapshot) => snapshots.push(snapshot));

    const blocker = createDeferred<void>();
    void runBudgeted("climate", () => blocker.promise);
    void runBudgeted("climate", () => Promise.resolve());
    void runBudgeted("drought", () => Promise.resolve());
    await vi.advanceTimersByTimeAsync(0);

    const current = getRequestBudgetSnapshot();
    expect(current.activeCount).toBe(1);
    expect(current.queuedByLane.get("climate")).toBe(1);
    expect(current.queuedByLane.get("drought")).toBe(1);
    expect(getQueuedRequestCountForLane("climate")).toBe(1);
    expect(getQueuedRequestCountForLane("drought")).toBe(1);
    expect(snapshots.length).toBeGreaterThan(0);

    unsubscribe();
    blocker.resolve();
    await vi.advanceTimersByTimeAsync(0);
  });
});

describe("SSR-safety", () => {
  it("never references window or navigator, anywhere in the module", () => {
    const thisDirectory = dirname(fileURLToPath(import.meta.url));
    const modulePath = resolvePath(thisDirectory, "../../../lib/net/request-budget.ts");
    const source = readFileSync(modulePath, "utf8");
    expect(source).not.toMatch(/\bwindow\b/);
    expect(source).not.toMatch(/\bnavigator\b/);
  });

  it("imports fresh and exercises basic operations without throwing", async () => {
    vi.resetModules();
    const freshModule = await import("@/lib/net/request-budget");
    expect(() => freshModule.getActiveRequestCount()).not.toThrow();
    expect(() => freshModule.getRequestBudgetSnapshot()).not.toThrow();
    const unsubscribe = freshModule.subscribeToRequestBudget(() => {});
    unsubscribe();
    freshModule.resetRequestBudgetForTests();
  });
});
