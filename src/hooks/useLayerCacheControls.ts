"use client";

/**
 * The whole per-layer cache control surface, as one hook.
 *
 * It is the ONLY place the three layers of this feature meet: the persisted preference store
 * (`src/lib/cache/layer-cache-policy-store.ts`), the IndexedDB cache that enforces them
 * (`src/lib/cache/query-persister.ts`) and the react-query client that has to be told when a
 * refetch was asked for. Keeping the meeting point here rather than in either module is what
 * lets the store stay free of an import of the persister -- see that store's header on why a
 * cycle between two module singletons is the thing being avoided. See `src/hooks/AGENTS.md`
 * §useLayerCacheControls.
 *
 * A UI surface for this lives in the layer panel's tree, not here; this hook is the entire API
 * such a component needs.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  attributeQueryKey,
  enforceLayerRetention,
  type RetentionSweepResult,
} from "@/lib/cache/query-persister";
import {
  layerCacheNature,
  type LayerCacheNature,
  type LayerRefreshMode,
} from "@/lib/cache/layer-cache-policy";
import {
  requestLayerRefresh,
  useLayerCachePolicyOverridden,
  useLayerCachePolicyStore,
  useLayerLastFetchedAt,
  useLayerRefreshMode,
  useLayerRetainedDayLimit,
} from "@/lib/cache/layer-cache-policy-store";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import {
  clearLayerSyncedDays,
  useLayerSyncedBytes,
  useSyncIndexReady,
  useSyncedDays,
} from "@/stores/sync-index-store";

/** Everything a per-layer cache control needs to render itself and to act. */
export interface LayerCacheControls {
  layerId: LayerToggleId;
  /** Where the defaults come from; render it so the default is explicable rather than magic. */
  nature: LayerCacheNature;
  refreshMode: LayerRefreshMode;
  /** Distinct days kept locally; `null` is unlimited (the 512 MB global budget still applies). */
  retainedDayLimit: number | null;
  /** True when the user has departed from the nature default in any way. */
  isOverridden: boolean;

  /**
   * Distinct days of this layer currently held on disk. Meaningful only while `isHeldKnown`;
   * "nothing is held" and "not yet read from IndexedDB" must not render the same.
   */
  heldDayCount: number;
  heldBytes: number;
  isHeldKnown: boolean;
  /** Epoch ms of the last real network answer written for this layer; `null` if never. */
  lastFetchedAt: number | null;

  isRefreshing: boolean;
  isClearing: boolean;

  setRefreshMode: (mode: LayerRefreshMode) => void;
  /** Applies the new limit AND evicts down to it immediately; `null` is unlimited. */
  setRetainedDayLimit: (limit: number | null) => Promise<void>;
  /** Back to the nature default, evicting immediately if that default is tighter. */
  resetToDefaults: () => Promise<void>;
  /** The manual refetch. Resolves once every mounted query for this layer has refetched. */
  refetchNow: () => Promise<void>;
  /** Drops everything held for this layer on disk, and every inactive query entry over it. */
  clearHeldData: () => Promise<void>;
}

export function useLayerCacheControls(layerId: LayerToggleId): LayerCacheControls {
  const queryClient = useQueryClient();
  const refreshMode = useLayerRefreshMode(layerId);
  const retainedDayLimit = useLayerRetainedDayLimit(layerId);
  const isOverridden = useLayerCachePolicyOverridden(layerId);
  const lastFetchedAt = useLayerLastFetchedAt(layerId);
  const heldDays = useSyncedDays(layerId);
  const heldBytes = useLayerSyncedBytes(layerId);
  const isHeldKnown = useSyncIndexReady();

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isClearing, setIsClearing] = useState(false);

  // Every action below is async and every one of them can outlive the panel it was clicked in.
  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const setRefreshMode = useCallback(
    (mode: LayerRefreshMode) => {
      useLayerCachePolicyStore.getState().setRefreshMode(layerId, mode);
    },
    [layerId]
  );

  const setRetainedDayLimit = useCallback(
    async (limit: number | null): Promise<void> => {
      useLayerCachePolicyStore.getState().setRetainedDayLimit(layerId, limit);
      // Swept HERE and not left to the next write: a limit that only takes effect the next time
      // the reader happens to land on a new day is a control whose effect cannot be observed,
      // and the held-days count beside it would go on contradicting the number just set.
      await enforceLayerRetention(layerId);
    },
    [layerId]
  );

  const resetToDefaults = useCallback(async (): Promise<void> => {
    useLayerCachePolicyStore.getState().resetPolicy(layerId);
    await enforceLayerRetention(layerId);
  }, [layerId]);

  const refetchNow = useCallback(async (): Promise<void> => {
    setIsRefreshing(true);
    try {
      // Stamped BEFORE the invalidation, and that order is the mechanism: the persister refuses
      // any entry created before this instant, so the refetch the invalidation triggers cannot
      // be answered by the copy it was meant to replace.
      requestLayerRefresh(layerId);
      await queryClient.invalidateQueries({
        predicate: (query) => attributeQueryKey(query.queryKey).layerId === layerId,
      });
    } finally {
      if (isMounted.current) setIsRefreshing(false);
    }
  }, [layerId, queryClient]);

  const clearHeldData = useCallback(async (): Promise<void> => {
    setIsClearing(true);
    try {
      await clearLayerSyncedDays(layerId);
      // Inactive entries only. Removing an ACTIVE one makes react-query refetch it on the spot,
      // which would write the layer straight back to disk and make "clear" look like it failed;
      // what is on screen stays on screen, and what is not is gone from memory as well as disk.
      queryClient.removeQueries({
        predicate: (query) => attributeQueryKey(query.queryKey).layerId === layerId,
        type: "inactive",
      });
    } finally {
      if (isMounted.current) setIsClearing(false);
    }
  }, [layerId, queryClient]);

  return {
    layerId,
    nature: layerCacheNature(layerId),
    refreshMode,
    retainedDayLimit,
    isOverridden,
    heldDayCount: heldDays.size,
    heldBytes,
    isHeldKnown,
    lastFetchedAt,
    isRefreshing,
    isClearing,
    setRefreshMode,
    setRetainedDayLimit,
    resetToDefaults,
    refetchNow,
    clearHeldData,
  };
}

/**
 * The same retention sweep the hook runs, for a caller that is not a component (a settings
 * import, a test). Resolves `null` when the layer has no limit or the store cannot be read.
 */
export async function sweepLayerRetention(
  layerId: LayerToggleId
): Promise<RetentionSweepResult | null> {
  return enforceLayerRetention(layerId);
}
