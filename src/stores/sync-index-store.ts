/**
 * The in-memory index of which days each layer already holds on local disk, and the
 * per-timeline reset that empties one layer's share of it.
 *
 * It is a PROJECTION of `src/lib/cache/query-persister.ts`'s IndexedDB store, never a second
 * copy of the data: one metadata read on mount builds it, and every later write or drop reaches
 * it incrementally through `CacheIndexObserver`. Answering "which days are synced?" by scanning
 * IndexedDB would mean a full pass per render, which is why the pass happens once and the answer
 * is kept here. See src/lib/cache/AGENTS.md "the synced-day index" for the coherence rule and
 * the ways it may drift.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  clearLayerEntries,
  scanSyncedDays,
  setCacheIndexObserver,
  type CacheIndexDropEvent,
  type CacheIndexEntryEvent,
  type SyncedDayFacts,
  type SyncedDayIndex,
} from "@/lib/cache/query-persister";
import type { LayerToggleId } from "@/lib/map/layer-registry";

/**
 * `unavailable` is not `ready` with nothing in it. IndexedDB absent (SSR, jsdom, private
 * browsing) means the question cannot be answered at all, and a track that drew "no days
 * synced" for it would be stating something it does not know.
 */
export type SyncIndexStatus = "idle" | "hydrating" | "ready" | "unavailable";

/** One layer's share of the index. `days` is derived, and replaced only when it changes. */
interface LayerSyncState {
  /** day -> facts. The authoritative record; `days` and `approxByteSize` are read off it. */
  facts: ReadonlyMap<string, SyncedDayFacts>;
  /**
   * The same days as a Set. Held rather than derived per call because the slider re-renders on
   * every scrub tick and a fresh Set each time would invalidate its band-geometry memo on
   * every frame.
   */
  days: ReadonlySet<string>;
  approxByteSize: number;
}

interface SyncIndexState {
  status: SyncIndexStatus;
  byLayer: ReadonlyMap<LayerToggleId, LayerSyncState>;
  hydrate: () => Promise<void>;
  clearLayer: (layerId: LayerToggleId) => Promise<void>;
  /** Drops days whose newest entry has passed its TTL; armed by a timer, never by a render. */
  pruneExpired: () => void;
}

/** Returned for every layer with nothing synced, so the identity a selector sees never changes. */
const NO_DAYS: ReadonlySet<string> = new Set<string>();

const EMPTY_INDEX: ReadonlyMap<LayerToggleId, LayerSyncState> = new Map();

/**
 * Ceiling on how far ahead the prune timer may be armed. A historical entry's TTL is 30 days
 * and `setTimeout` silently fires immediately past its 32-bit millisecond limit (~24.8 days),
 * so this clamp is load-bearing rather than tidy; the timer simply re-arms.
 */
const MAX_PRUNE_DELAY_MS = 6 * 60 * 60 * 1000;

let expiryPruneTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Writes seen while the hydration pass was in flight, replayed once its result is applied.
 *
 * The pass's snapshot is older than a write that lands during it, so applying the snapshot alone
 * would drop that day from the index until the next mount. Each is tagged with its cache key, so
 * a write the pass ALREADY saw is skipped rather than counted twice. Null except during a pass.
 */
let writesDuringWalk: CacheIndexEntryEvent[] | null = null;

function layerStateFromFacts(facts: ReadonlyMap<string, SyncedDayFacts>): LayerSyncState {
  let approxByteSize = 0;
  for (const dayFacts of facts.values()) approxByteSize += dayFacts.approxByteSize;
  return { facts, days: new Set(facts.keys()), approxByteSize };
}

/** True when two day maps say exactly the same thing, so the old state object can be kept. */
function sameFacts(
  left: ReadonlyMap<string, SyncedDayFacts>,
  right: ReadonlyMap<string, SyncedDayFacts>
): boolean {
  if (left.size !== right.size) return false;
  for (const [day, facts] of left) {
    const other = right.get(day);
    if (
      other === undefined ||
      other.expiresAt !== facts.expiresAt ||
      other.approxByteSize !== facts.approxByteSize ||
      other.entryCount !== facts.entryCount
    ) {
      return false;
    }
  }
  return true;
}

/**
 * Rebuilds the whole index from a walk, keeping the previous state object for any layer whose
 * days are unchanged -- a rebuild that replaced every layer would re-render every slider on the
 * page for nothing.
 */
function indexToLayerStates(
  index: SyncedDayIndex,
  previous: ReadonlyMap<LayerToggleId, LayerSyncState>
): ReadonlyMap<LayerToggleId, LayerSyncState> {
  const next = new Map<LayerToggleId, LayerSyncState>();
  for (const [layerId, facts] of index) {
    const before = previous.get(layerId);
    next.set(
      layerId,
      before !== undefined && sameFacts(before.facts, facts) ? before : layerStateFromFacts(facts)
    );
  }
  return next;
}

/** Folds one written entry into a layer's days, or returns the state unchanged when nothing moved. */
function withRecordedEntry(
  previous: ReadonlyMap<LayerToggleId, LayerSyncState>,
  event: CacheIndexEntryEvent
): ReadonlyMap<LayerToggleId, LayerSyncState> {
  const before = previous.get(event.layerId);
  const existing = before?.facts.get(event.day);
  if (
    existing !== undefined &&
    !event.isNewEntry &&
    event.byteDelta === 0 &&
    existing.expiresAt >= event.expiresAt
  ) {
    return previous;
  }
  const facts = new Map(before?.facts ?? []);
  facts.set(event.day, {
    expiresAt: Math.max(existing?.expiresAt ?? 0, event.expiresAt),
    approxByteSize: Math.max(0, (existing?.approxByteSize ?? 0) + event.byteDelta),
    // An overwrite of a day the index never saw still means at least one entry covers it.
    entryCount: Math.max(1, (existing?.entryCount ?? 0) + (event.isNewEntry ? 1 : 0)),
  });
  const next = new Map(previous);
  next.set(
    event.layerId,
    existing === undefined || before === undefined
      ? layerStateFromFacts(facts)
      : // The day was already listed, so the Set is unchanged and must keep its identity.
        {
          facts,
          days: before.days,
          approxByteSize: Math.max(0, before.approxByteSize + event.byteDelta),
        }
  );
  return next;
}

/**
 * Removes one entry's weight from a layer's days, dropping the day when its last entry goes.
 *
 * The day's `expiresAt` is left alone deliberately: the only drop that arrives incrementally is
 * an EXPIRED entry deleted on read, which can never be the newest expiry among the fresh entries
 * still covering that day. Eviction and the per-layer reset both republish a rebuilt index
 * instead, so neither depends on this.
 */
function withDroppedEntry(
  previous: ReadonlyMap<LayerToggleId, LayerSyncState>,
  event: CacheIndexDropEvent
): ReadonlyMap<LayerToggleId, LayerSyncState> {
  const before = previous.get(event.layerId);
  const existing = before?.facts.get(event.day);
  if (before === undefined || existing === undefined) return previous;
  const facts = new Map(before.facts);
  const entryCount = existing.entryCount - 1;
  if (entryCount <= 0) {
    facts.delete(event.day);
  } else {
    facts.set(event.day, {
      expiresAt: existing.expiresAt,
      approxByteSize: Math.max(0, existing.approxByteSize - event.approxByteSize),
      entryCount,
    });
  }
  const next = new Map(previous);
  if (facts.size === 0) {
    next.delete(event.layerId);
  } else if (entryCount <= 0) {
    next.set(event.layerId, layerStateFromFacts(facts));
  } else {
    // The day survives, so the Set is unchanged and must keep its identity.
    next.set(event.layerId, {
      facts,
      days: before.days,
      approxByteSize: Math.max(0, before.approxByteSize - event.approxByteSize),
    });
  }
  return next;
}

/** Arms one timer for the moment the soonest-expiring day stops being fresh. */
function scheduleExpiryPrune(byLayer: ReadonlyMap<LayerToggleId, LayerSyncState>): void {
  if (expiryPruneTimer !== null) {
    clearTimeout(expiryPruneTimer);
    expiryPruneTimer = null;
  }
  let soonest = Number.POSITIVE_INFINITY;
  for (const layer of byLayer.values()) {
    for (const facts of layer.facts.values()) {
      if (facts.expiresAt < soonest) soonest = facts.expiresAt;
    }
  }
  if (!Number.isFinite(soonest)) return;
  const delay = Math.min(Math.max(soonest - Date.now(), 0), MAX_PRUNE_DELAY_MS);
  expiryPruneTimer = setTimeout(() => {
    expiryPruneTimer = null;
    useSyncIndexStore.getState().pruneExpired();
  }, delay);
  // Never hold a node process -- or a vitest run -- open just to prune a display index.
  (expiryPruneTimer as unknown as { unref?: () => void }).unref?.();
}

/** The projection this module registers on the cache; the cache never imports the store back. */
const cacheIndexObserver = {
  entryStored(event: CacheIndexEntryEvent): void {
    if (writesDuringWalk !== null) writesDuringWalk.push(event);
    const previous = useSyncIndexStore.getState().byLayer;
    const byLayer = withRecordedEntry(previous, event);
    if (byLayer === previous) return;
    useSyncIndexStore.setState({ byLayer }, false, "sync-index/entry-stored");
    scheduleExpiryPrune(byLayer);
  },
  entryDropped(event: CacheIndexDropEvent): void {
    const previous = useSyncIndexStore.getState().byLayer;
    const byLayer = withDroppedEntry(previous, event);
    if (byLayer === previous) return;
    useSyncIndexStore.setState({ byLayer }, false, "sync-index/entry-dropped");
    scheduleExpiryPrune(byLayer);
  },
  indexRebuilt(index: SyncedDayIndex): void {
    const byLayer = indexToLayerStates(index, useSyncIndexStore.getState().byLayer);
    useSyncIndexStore.setState({ byLayer }, false, "sync-index/rebuilt");
    scheduleExpiryPrune(byLayer);
  },
};

export const useSyncIndexStore = create<SyncIndexState>()(
  devtools(
    (set, get) => ({
      status: "idle",
      byLayer: EMPTY_INDEX,

      hydrate: async () => {
        // Registered before the status guard, not inside it: React's StrictMode mounts the
        // hydration hook twice, and a registration that lived on the first call's success path
        // would be dropped by the second call's early return.
        setCacheIndexObserver(cacheIndexObserver);
        const status = get().status;
        if (status === "hydrating" || status === "ready") return;
        set({ status: "hydrating" }, false, "sync-index/hydrating");
        writesDuringWalk = [];
        const scan = await scanSyncedDays();
        const replay = writesDuringWalk ?? [];
        writesDuringWalk = null;
        if (scan === null) {
          set({ status: "unavailable" }, false, "sync-index/unavailable");
          return;
        }
        let byLayer = indexToLayerStates(scan.index, get().byLayer);
        for (const event of replay) {
          // The scan already counted anything it saw on disk; replaying it would double-count.
          if (scan.coveredKeys.has(event.cacheKey)) continue;
          byLayer = withRecordedEntry(byLayer, event);
        }
        set({ status: "ready", byLayer }, false, "sync-index/ready");
        scheduleExpiryPrune(byLayer);
      },

      clearLayer: async (layerId) => {
        const index = await clearLayerEntries(layerId);
        // `null` means the store could not be read, so the reset cannot be claimed: leaving the
        // index alone under-reports nothing and never asserts a deletion that did not happen.
        if (index === null) return;
        const byLayer = indexToLayerStates(index, get().byLayer);
        set({ byLayer }, false, "sync-index/cleared");
        scheduleExpiryPrune(byLayer);
      },

      pruneExpired: () => {
        const now = Date.now();
        const previous = get().byLayer;
        let changed = false;
        const next = new Map<LayerToggleId, LayerSyncState>();
        for (const [layerId, layer] of previous) {
          const fresh = new Map<string, SyncedDayFacts>();
          for (const [day, facts] of layer.facts) {
            if (now < facts.expiresAt) fresh.set(day, facts);
          }
          if (fresh.size === layer.facts.size) {
            next.set(layerId, layer);
            continue;
          }
          changed = true;
          if (fresh.size > 0) next.set(layerId, layerStateFromFacts(fresh));
        }
        if (!changed) {
          scheduleExpiryPrune(previous);
          return;
        }
        set({ byLayer: next }, false, "sync-index/pruned");
        scheduleExpiryPrune(next);
      },
    }),
    { name: "sync-index" }
  )
);

/**
 * Days (YYYY-MM-DD) this layer currently holds FRESH on local disk.
 *
 * Keyed on the day alone and not on (day, bbox): one day accumulates a separate entry per
 * viewport the reader visited it at -- the bbox is serialized to six decimal places, so a pan
 * of a few centimetres mints another -- and a track that only lit a day when THIS viewport's
 * entry was cached would be dark almost always. It therefore answers "an answer for this day is
 * on disk", not "the next read of this day will certainly hit".
 */
export function useSyncedDays(layerId: LayerToggleId): ReadonlySet<string> {
  return useSyncIndexStore((state) => state.byLayer.get(layerId)?.days ?? NO_DAYS);
}

/** False until the index has been read from IndexedDB once; the UI shows nothing rather than lying. */
export function useSyncIndexReady(): boolean {
  return useSyncIndexStore((state) => state.status === "ready");
}

/** Approximate bytes this layer occupies on disk, for the reset control's label. */
export function useLayerSyncedBytes(layerId: LayerToggleId): number {
  return useSyncIndexStore((state) => state.byLayer.get(layerId)?.approxByteSize ?? 0);
}

/** Per-timeline reset: drops every persisted entry belonging to one layer. Never throws. */
export async function clearLayerSyncedDays(layerId: LayerToggleId): Promise<void> {
  await useSyncIndexStore.getState().clearLayer(layerId);
}

/** Called once near the app root -- see `Providers` in src/lib/providers.tsx. */
export function useHydrateSyncIndex(): void {
  useEffect(() => {
    void useSyncIndexStore.getState().hydrate();
  }, []);
}

/** Exported for tests only: forgets the index and unregisters the cache observer. */
export function resetSyncIndexForTests(): void {
  if (expiryPruneTimer !== null) {
    clearTimeout(expiryPruneTimer);
    expiryPruneTimer = null;
  }
  writesDuringWalk = null;
  setCacheIndexObserver(null);
  useSyncIndexStore.setState({ status: "idle", byLayer: EMPTY_INDEX }, false, "sync-index/reset");
}
