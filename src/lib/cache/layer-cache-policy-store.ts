/**
 * The persisted, per-layer cache preferences: the user's overrides, the moment they last asked
 * for a refetch, and the moment each layer last actually reached the network.
 *
 * It lives beside the cache it governs rather than in `src/stores/` for one structural reason:
 * `query-persister.ts` READS this store on every hit and write, exactly the way it already reads
 * `useTimeSliderStore` for `serverCurrentDate`. If this module imported the persister back --
 * to run a retention sweep, say -- that would be a cycle between two module singletons, which is
 * the failure mode `sync-index-store.ts` avoids with a callback seam. So the dependency runs one
 * way only (persister -> policy store), and every action that has to touch IndexedDB lives in
 * `src/hooks/useLayerCacheControls.ts`, above both. See src/lib/cache/AGENTS.md
 * "per-layer cache policy".
 *
 * Persistence is zustand's `persist` over localStorage, hydrated SYNCHRONOUSLY at import, which
 * is what lets `layerCachePolicyFor()` answer correctly on the very first cache read of a session
 * with no mount-time registration step to forget.
 */

import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import {
  clampRetainedDayLimit,
  resolveLayerCachePolicy,
  sanitizeLayerCacheOverrides,
  sanitizeLayerTimestamps,
  type LayerCacheOverride,
  type LayerCachePolicy,
  type LayerRefreshMode,
} from "./layer-cache-policy";

interface LayerCachePolicyState {
  /** Sparse: an absent key is "follows its nature's default", which is not the same as a copy of it. */
  overrides: Partial<Record<LayerToggleId, LayerCacheOverride>>;
  /**
   * When the user last asked this layer to refetch. Every entry created strictly before it is a
   * MISS -- see `isSupersededByRefreshRequest` -- which is how one click reaches days the reader
   * is not currently looking at without deleting them up front.
   */
  refreshRequestedAt: Partial<Record<LayerToggleId, number>>;
  /**
   * When this layer last actually reached the network and wrote an entry. Persisted because a
   * control that says "last fetched" and forgets on reload is answering a different question
   * from the one the user asked.
   */
  lastFetchedAt: Partial<Record<LayerToggleId, number>>;

  setRefreshMode: (layerId: LayerToggleId, mode: LayerRefreshMode) => void;
  setRetainedDayLimit: (layerId: LayerToggleId, limit: number | null) => void;
  /** Drops the whole override, so the layer follows its nature again. */
  resetPolicy: (layerId: LayerToggleId) => void;
  /** Stamps the refresh request and returns the instant recorded, for the caller to await against. */
  requestRefresh: (layerId: LayerToggleId) => number;
  recordFetched: (layerId: LayerToggleId, at: number) => void;
}

const PERSIST_KEY = "plantgeo-layer-cache-policy";

/** Returned for a layer with no override, so a selector's identity never changes for that layer. */
const NO_OVERRIDE: LayerCacheOverride | undefined = undefined;

function withOverride(
  previous: Partial<Record<LayerToggleId, LayerCacheOverride>>,
  layerId: LayerToggleId,
  patch: LayerCacheOverride
): Partial<Record<LayerToggleId, LayerCacheOverride>> {
  return { ...previous, [layerId]: { ...previous[layerId], ...patch } };
}

export const useLayerCachePolicyStore = create<LayerCachePolicyState>()(
  devtools(
    persist(
      (set) => ({
        overrides: {},
        refreshRequestedAt: {},
        lastFetchedAt: {},

        setRefreshMode: (layerId, mode) =>
          set(
            (s) => ({ overrides: withOverride(s.overrides, layerId, { refreshMode: mode }) }),
            false,
            "layer-cache-policy/refresh-mode"
          ),

        // Clamped here rather than at the call site: this is the one writer, so a slider that
        // hands over 0 -- or a rehydrated blob replayed through it -- can never reach the sweep.
        setRetainedDayLimit: (layerId, limit) =>
          set(
            (s) => ({
              overrides: withOverride(s.overrides, layerId, {
                retainedDayLimit: clampRetainedDayLimit(limit),
              }),
            }),
            false,
            "layer-cache-policy/retained-day-limit"
          ),

        resetPolicy: (layerId) =>
          set(
            (s) => {
              if (s.overrides[layerId] === undefined) return s;
              const overrides = { ...s.overrides };
              delete overrides[layerId];
              return { overrides };
            },
            false,
            "layer-cache-policy/reset"
          ),

        requestRefresh: (layerId) => {
          const at = Date.now();
          set(
            (s) => ({ refreshRequestedAt: { ...s.refreshRequestedAt, [layerId]: at } }),
            false,
            "layer-cache-policy/refresh-requested"
          );
          return at;
        },

        recordFetched: (layerId, at) =>
          set(
            (s) =>
              (s.lastFetchedAt[layerId] ?? 0) >= at
                ? s
                : { lastFetchedAt: { ...s.lastFetchedAt, [layerId]: at } },
            false,
            "layer-cache-policy/fetched"
          ),
      }),
      {
        // Follows layer-store's precedent: devtools(persist) with an explicit `partialize` and a
        // `merge` that re-sanitizes, because localStorage is user-writable and this blob decides
        // both what is evicted and how long an entry is trusted.
        name: PERSIST_KEY,
        version: 1,
        partialize: (s) => ({
          overrides: s.overrides,
          refreshRequestedAt: s.refreshRequestedAt,
          lastFetchedAt: s.lastFetchedAt,
        }),
        merge: (persisted, current) => {
          const blob = persisted as
            | { overrides?: unknown; refreshRequestedAt?: unknown; lastFetchedAt?: unknown }
            | undefined;
          return {
            ...current,
            overrides: sanitizeLayerCacheOverrides(blob?.overrides),
            refreshRequestedAt: sanitizeLayerTimestamps(blob?.refreshRequestedAt),
            lastFetchedAt: sanitizeLayerTimestamps(blob?.lastFetchedAt),
          };
        },
      }
    ),
    { name: "layer-cache-policy" }
  )
);

/* --------------------------------------------------------------------------------------------
 * Non-React accessors. `query-persister.ts` is not a component and runs on the cache path, so
 * everything it needs is a plain `getState()` read -- the same shape it already uses for
 * `useTimeSliderStore.getState().capabilities`.
 * ------------------------------------------------------------------------------------------ */

/** The policy in force for a layer. `null` (an unattributable query) gets the fallback nature. */
export function layerCachePolicyFor(layerId: LayerToggleId | null): LayerCachePolicy {
  const override =
    layerId === null ? NO_OVERRIDE : useLayerCachePolicyStore.getState().overrides[layerId];
  return resolveLayerCachePolicy(layerId, override);
}

/**
 * True when the user asked this layer to refetch AFTER the entry in hand was created.
 *
 * Strictly `<`: an entry written in the same millisecond as the request is the answer the
 * request asked for, not the one it is replacing.
 */
export function isSupersededByRefreshRequest(
  layerId: LayerToggleId | null,
  createdAt: number
): boolean {
  if (layerId === null) return false;
  const requestedAt = useLayerCachePolicyStore.getState().refreshRequestedAt[layerId];
  return requestedAt !== undefined && createdAt < requestedAt;
}

/** Stamps a manual refetch request and returns the instant recorded. */
export function requestLayerRefresh(layerId: LayerToggleId): number {
  return useLayerCachePolicyStore.getState().requestRefresh(layerId);
}

/** Called by the persister whenever a layer's entry is written from a real network answer. */
export function recordLayerFetched(layerId: LayerToggleId, at: number): void {
  useLayerCachePolicyStore.getState().recordFetched(layerId, at);
}

/* --------------------------------------------------------------------------------------------
 * React selectors. Every one returns a PRIMITIVE: zustand v5 re-runs a selector on each store
 * read and compares by identity, so a selector building a fresh policy object would re-render
 * forever.
 * ------------------------------------------------------------------------------------------ */

export function useLayerRefreshMode(layerId: LayerToggleId): LayerRefreshMode {
  return useLayerCachePolicyStore(
    (s) => resolveLayerCachePolicy(layerId, s.overrides[layerId]).refreshMode
  );
}

export function useLayerRetainedDayLimit(layerId: LayerToggleId): number | null {
  return useLayerCachePolicyStore(
    (s) => resolveLayerCachePolicy(layerId, s.overrides[layerId]).retainedDayLimit
  );
}

/** True when the user has departed from this layer's nature default in any way. */
export function useLayerCachePolicyOverridden(layerId: LayerToggleId): boolean {
  return useLayerCachePolicyStore(
    (s) => resolveLayerCachePolicy(layerId, s.overrides[layerId]).isOverridden
  );
}

/** Epoch ms of the last real network fetch for this layer, or `null` if it has never fetched. */
export function useLayerLastFetchedAt(layerId: LayerToggleId): number | null {
  return useLayerCachePolicyStore((s) => s.lastFetchedAt[layerId] ?? null);
}

/** Exported for tests only: forgets every preference AND the persisted blob behind it. */
export function resetLayerCachePolicyForTests(): void {
  useLayerCachePolicyStore.setState(
    { overrides: {}, refreshRequestedAt: {}, lastFetchedAt: {} },
    false,
    "layer-cache-policy/test-reset"
  );
  try {
    globalThis.localStorage?.removeItem(PERSIST_KEY);
  } catch {
    // No localStorage in this environment; there was nothing to clear.
  }
}

/** Exported for tests only: the localStorage key the persisted blob is written under. */
export const LAYER_CACHE_POLICY_PERSIST_KEY = PERSIST_KEY;
