/**
 * A single, framework-agnostic request budget shared by every network-issuing call site in the
 * client -- the "uniform convention for all layers" the owner asked for. Bounds BOTH how many
 * requests may be in flight at once (concurrency) and how fast new ones may start (a token-
 * bucket rate limiter), schedules queued work fairly across named "lanes" so one bursty stream
 * can never starve another, and lets any request -- queued or already dispatched -- be dropped
 * for free via the `AbortSignal` idiom this codebase already uses (see `useFireData.ts`).
 *
 * Deliberately has no knowledge of fetch, tRPC, or React: `acquireRequestSlot` / `runBudgeted`
 * take a plain async task, so a raw `fetch()` call site and a batched tRPC link can share the
 * exact same budget. `createBudgetedFetch` is a convenience adapter for the fetch shape
 * specifically, since that covers every call site this module was built for.
 *
 * This is a module-level singleton on purpose: nothing here is constructable, so a caller
 * cannot stand up a private budget and quietly opt out of the shared one. See
 * src/lib/net/AGENTS.md for the concrete numbers, the fairness policy, and the six pre-existing
 * request-shaping mechanisms this is meant to eventually subsume.
 */

/** A named stream of requests (one map layer, one offline-sync queue, ...). Fairness only -- never priority. */
export type RequestLane = string;

/** Global ceiling: at most this many requests, across every lane combined, in flight at once. */
export const MAX_CONCURRENT_REQUESTS = 4;

/** Token-bucket steady-state refill: new dispatch permits minted per second once the burst is spent. */
export const SUSTAINED_REQUESTS_PER_SECOND = 5;

/** Token-bucket capacity: how many dispatches a fully-rested budget allows instantly before rate-limiting bites. */
export const BURST_CAPACITY = 8;

/** A granted slot. `release()` must be called when the work is done; safe to call more than once. */
export interface RequestSlotHandle {
  release(): void;
}

export interface AcquireRequestSlotOptions {
  /** Aborting drops a still-queued request for free; an already-dispatched one is freed too, see AGENTS.md. */
  signal?: AbortSignal;
}

/** Point-in-time counts for a pending/queued UI indicator; see `getRequestBudgetSnapshot`. */
export interface RequestBudgetSnapshot {
  activeCount: number;
  queuedCount: number;
  queuedByLane: ReadonlyMap<RequestLane, number>;
}

type RequestBudgetListener = (snapshot: RequestBudgetSnapshot) => void;

interface QueueEntry {
  dispatch: () => void;
}

let maxConcurrent: number = MAX_CONCURRENT_REQUESTS;
let ratePerSecond: number = SUSTAINED_REQUESTS_PER_SECOND;
let burstCapacity: number = BURST_CAPACITY;

let activeCount = 0;
let tokens: number = BURST_CAPACITY;
/** Lazily set on first use, not at module load -- so real wall-clock time at import can never
 *  leak into a fake-timer test whose clock is installed/frozen after this module first runs. */
let lastRefillAtMs: number | null = null;

const queueByLane = new Map<RequestLane, QueueEntry[]>();
/** FIFO of lanes waiting for a turn; a lane appears at most once (see `lanesInRotationQueue`). */
const rotationQueue: RequestLane[] = [];
const lanesInRotationQueue = new Set<RequestLane>();

let refillTimer: ReturnType<typeof setTimeout> | null = null;

const listeners = new Set<RequestBudgetListener>();

/** Accrues tokens for elapsed wall-clock time, capped at the burst ceiling. */
function refillTokens(): void {
  const nowMs = Date.now();
  if (lastRefillAtMs === null) {
    lastRefillAtMs = nowMs;
    return;
  }
  const elapsedSeconds = Math.max(0, nowMs - lastRefillAtMs) / 1000;
  if (elapsedSeconds <= 0) return;
  tokens = Math.min(burstCapacity, tokens + elapsedSeconds * ratePerSecond);
  lastRefillAtMs = nowMs;
}

function millisecondsUntilNextToken(): number {
  if (tokens >= 1) return 0;
  const deficit = 1 - tokens;
  return Math.max(1, Math.ceil((deficit / ratePerSecond) * 1000));
}

/** Schedules exactly one wake-up for whenever the next token will exist; a no-op if one is already pending. */
function ensureRefillWake(): void {
  if (refillTimer !== null) return;
  refillTimer = setTimeout(() => {
    refillTimer = null;
    tryDispatchNext();
  }, millisecondsUntilNextToken());
}

function enqueue(lane: RequestLane, entry: QueueEntry): void {
  let queue = queueByLane.get(lane);
  if (!queue) {
    queue = [];
    queueByLane.set(lane, queue);
  }
  queue.push(entry);
  if (!lanesInRotationQueue.has(lane)) {
    lanesInRotationQueue.add(lane);
    rotationQueue.push(lane);
  }
}

function removeFromLaneQueue(lane: RequestLane, entry: QueueEntry): void {
  const queue = queueByLane.get(lane);
  if (!queue) return;
  const index = queue.indexOf(entry);
  if (index !== -1) queue.splice(index, 1);
  // Deliberately does not touch rotationQueue/lanesInRotationQueue: pickNextLaneWithWork below
  // discards a lane it pops with nothing left to serve, so a reference left stale by this
  // removal is self-healing on its next turn rather than something to reconcile here.
}

/** Pops the next lane whose turn it is AND that still has work, skipping any left stale by an abort. */
function pickNextLaneWithWork(): RequestLane | null {
  while (rotationQueue.length > 0) {
    const lane = rotationQueue.shift()!;
    lanesInRotationQueue.delete(lane);
    const queue = queueByLane.get(lane);
    if (queue && queue.length > 0) return lane;
  }
  return null;
}

function totalQueuedCount(): number {
  let total = 0;
  for (const queue of queueByLane.values()) total += queue.length;
  return total;
}

function snapshot(): RequestBudgetSnapshot {
  const queuedByLane = new Map<RequestLane, number>();
  for (const [lane, queue] of queueByLane) {
    if (queue.length > 0) queuedByLane.set(lane, queue.length);
  }
  return { activeCount, queuedCount: totalQueuedCount(), queuedByLane };
}

function notifyListeners(): void {
  if (listeners.size === 0) return;
  const current = snapshot();
  for (const listener of listeners) listener(current);
}

/**
 * Round-robin across lanes with pending work, not plain FIFO: a lane is re-queued to the BACK
 * of the rotation immediately after serving one of its requests, so a lane that just arrived
 * with one request gets it dispatched after at most one extra turn from each lane already
 * waiting -- never after an entire burst from a single lane drains. See AGENTS.md "fairness
 * policy" for the worked 9-vs-1 example this is built to satisfy.
 */
function tryDispatchNext(): void {
  let dispatchedAny = false;
  while (activeCount < maxConcurrent) {
    refillTokens();
    if (tokens < 1) {
      ensureRefillWake();
      break;
    }
    const lane = pickNextLaneWithWork();
    if (lane === null) break;
    const queue = queueByLane.get(lane)!;
    const entry = queue.shift()!;
    if (queue.length > 0) {
      lanesInRotationQueue.add(lane);
      rotationQueue.push(lane);
    }
    tokens -= 1;
    activeCount += 1;
    dispatchedAny = true;
    entry.dispatch();
  }
  if (dispatchedAny) notifyListeners();
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

/**
 * Resolves once BOTH a concurrency slot and a rate-limit token are available, fairly. The
 * returned handle's `release()` must be called when the work is done -- prefer `runBudgeted`,
 * which does this for you and cannot leak a slot on any exit path.
 *
 * Rejects immediately (never entering the queue) if `signal` is already aborted, and rejects
 * cheaply -- without ever having consumed a slot or a token -- if it aborts while still queued.
 * Aborting an already-dispatched request releases its slot immediately too, rather than waiting
 * on the caller's own cleanup; see AGENTS.md for why that is safe.
 */
export function acquireRequestSlot(
  lane: RequestLane,
  options: AcquireRequestSlotOptions = {}
): Promise<RequestSlotHandle> {
  const { signal } = options;
  if (signal?.aborted) return Promise.reject(abortError(signal));

  return new Promise<RequestSlotHandle>((resolve, reject) => {
    let dispatched = false;
    let released = false;
    let entry: QueueEntry;

    const release = (): void => {
      if (released) return;
      released = true;
      signal?.removeEventListener("abort", handleAbort);
      activeCount = Math.max(0, activeCount - 1);
      tryDispatchNext();
      notifyListeners();
    };

    const handleAbort = (): void => {
      signal?.removeEventListener("abort", handleAbort);
      if (dispatched) {
        // Already running: free its slot immediately instead of waiting on the caller's own
        // cleanup, so an aborted request can never hold capacity hostage.
        release();
        return;
      }
      removeFromLaneQueue(lane, entry);
      reject(abortError(signal!));
      notifyListeners();
    };

    entry = {
      dispatch: () => {
        // The abort listener stays attached deliberately: an abort that arrives AFTER dispatch
        // must still free the slot immediately (see handleAbort's `dispatched` branch above).
        dispatched = true;
        resolve({ release });
      },
    };

    signal?.addEventListener("abort", handleAbort, { once: true });
    enqueue(lane, entry);
    notifyListeners();
    tryDispatchNext();
  });
}

/**
 * Runs `task` once a slot and a token are available, and always releases the slot afterward --
 * on success, on a thrown error, or on an abort mid-flight. This is the API every call site
 * should reach for; `acquireRequestSlot` exists only for `createBudgetedFetch` below, which
 * needs the wait and the network call to be separate steps.
 */
export async function runBudgeted<T>(
  lane: RequestLane,
  task: (signal: AbortSignal | undefined) => Promise<T>,
  options: AcquireRequestSlotOptions = {}
): Promise<T> {
  const slot = await acquireRequestSlot(lane, options);
  try {
    return await task(options.signal);
  } finally {
    slot.release();
  }
}

/**
 * A `fetch`-shaped function bound to one lane -- pass it as `httpBatchLink({ fetch: ... })` to
 * cover every tRPC-routed layer with one lane in one place, or call it directly as a drop-in
 * replacement for a raw `fetch(...)` at a call site like `/api/fires`. Either way the caller's
 * own `init.signal` still cancels the real network call once dispatched; this only ever adds a
 * wait beforehand, and drops that wait for free if the same signal fires first.
 */
export function createBudgetedFetch(lane: RequestLane): typeof fetch {
  const budgetedFetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
    runBudgeted(lane, () => fetch(input, init), { signal: init?.signal ?? undefined });
  return budgetedFetch;
}

/** How many requests, across every lane, are dispatched and awaiting a response right now. */
export function getActiveRequestCount(): number {
  return activeCount;
}

/** How many requests, across every lane, are waiting for a slot and a token. */
export function getQueuedRequestCount(): number {
  return totalQueuedCount();
}

/** How many of one lane's requests are queued (not yet dispatched). */
export function getQueuedRequestCountForLane(lane: RequestLane): number {
  return queueByLane.get(lane)?.length ?? 0;
}

/** One consistent read of active/queued/per-lane state, for a UI indicator's initial render. */
export function getRequestBudgetSnapshot(): RequestBudgetSnapshot {
  return snapshot();
}

/** Fires on every acquire, dispatch and release; pairs naturally with `useSyncExternalStore`. */
export function subscribeToRequestBudget(listener: RequestBudgetListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export interface RequestBudgetTestOverrides {
  maxConcurrent?: number;
  ratePerSecond?: number;
  burstCapacity?: number;
}

/**
 * Exported for tests only -- production callers never reconfigure the shared budget; doing so
 * would reopen exactly the "private allowance" escape hatch this module exists to close.
 * Clears all queued/active bookkeeping and any pending timer, and re-applies the real constants
 * unless a test overrides them.
 */
export function resetRequestBudgetForTests(overrides: RequestBudgetTestOverrides = {}): void {
  if (refillTimer !== null) {
    clearTimeout(refillTimer);
    refillTimer = null;
  }
  maxConcurrent = Math.max(1, overrides.maxConcurrent ?? MAX_CONCURRENT_REQUESTS);
  ratePerSecond = Math.max(0.001, overrides.ratePerSecond ?? SUSTAINED_REQUESTS_PER_SECOND);
  burstCapacity = Math.max(1, overrides.burstCapacity ?? BURST_CAPACITY);
  activeCount = 0;
  tokens = burstCapacity;
  lastRefillAtMs = null;
  queueByLane.clear();
  rotationQueue.length = 0;
  lanesInRotationQueue.clear();
  listeners.clear();
}
