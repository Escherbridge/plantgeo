"use client";

import { useState, useEffect, useCallback } from "react";
import { dequeue, remove } from "@/lib/offline/sync-queue";

interface UseOfflineSyncResult {
  isOnline: boolean;
  pendingCount: number;
}

export function useOfflineSync(): UseOfflineSyncResult {
  const [isOnline, setIsOnline] = useState(
    typeof navigator !== "undefined" ? navigator.onLine : true
  );
  const [pendingCount, setPendingCount] = useState(0);

  const refreshPendingCount = useCallback(async () => {
    try {
      const ops = await dequeue();
      setPendingCount(ops.length);
    } catch {
      setPendingCount(0);
    }
  }, []);

  const runSync = useCallback(async () => {
    try {
      const ops = await dequeue();
      setPendingCount(ops.length);
      for (const op of ops) {
        try {
          const response = await fetch(`/api/${op.resource}`, {
            method:
              op.type === "create"
                ? "POST"
                : op.type === "update"
                  ? "PUT"
                  : "DELETE",
            headers: { "Content-Type": "application/json" },
            body: op.type !== "delete" ? JSON.stringify(op.data) : undefined,
          });
          if (response.ok) {
            await remove(op.id);
            setPendingCount((prev) => Math.max(0, prev - 1));
          }
        } catch {
          // leave in queue for next sync
        }
      }
    } catch {
      // IndexedDB unavailable
    }
  }, []);

  useEffect(() => {
    refreshPendingCount();
  }, [refreshPendingCount]);

  useEffect(() => {
    function handleOnline() {
      setIsOnline(true);
      runSync();
    }

    function handleOffline() {
      setIsOnline(false);
    }

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [runSync]);

  return { isOnline, pendingCount };
}
