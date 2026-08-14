"use client";

import { useState, useEffect, useCallback } from "react";
import {
  dequeue,
  remove,
  markConflicted,
  getConflicts,
  resolveConflict as resolveConflictOp,
  type SyncOperation,
  type ConflictedOperation,
} from "@/lib/offline/sync-queue";
import { toast } from "@/components/ui/toast";

interface UseOfflineSyncResult {
  isOnline: boolean;
  pendingCount: number;
  isSyncing: boolean;
  conflicts: ConflictedOperation[];
  resolveConflict: (id: string, resolution: "keep-local" | "discard-local") => Promise<void>;
}

/**
 * Best-effort staleness check: fetches the resource an update/delete targets and compares its
 * `updatedAt` against the op's snapshot. Fails open (returns false) on any network/shape
 * surprise -- the write attempt right after this is what's actually authoritative, and the
 * server's own 409 (checked separately) is the backstop if this pre-check misses something.
 */
async function isSnapshotStale(op: SyncOperation): Promise<boolean> {
  if (op.type === "create") return false; // nothing on the server yet to be stale against
  const record = op.data as { id?: string } | null;
  if (!record?.id) return false;

  try {
    const res = await fetch(`/api/${op.resource}/${record.id}`);
    if (!res.ok) return false;
    const current = await res.json();
    return typeof current?.updatedAt === "number" && current.updatedAt > op.updatedAt;
  } catch {
    return false;
  }
}

export function useOfflineSync(): UseOfflineSyncResult {
  const [isOnline, setIsOnline] = useState(
    typeof navigator !== "undefined" ? navigator.onLine : true
  );
  const [pendingCount, setPendingCount] = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);
  const [conflicts, setConflicts] = useState<ConflictedOperation[]>([]);

  const refreshPendingCount = useCallback(async () => {
    try {
      const ops = await dequeue();
      setPendingCount(ops.length);
    } catch {
      setPendingCount(0);
    }
  }, []);

  const refreshConflicts = useCallback(async () => {
    try {
      setConflicts(await getConflicts());
    } catch {
      setConflicts([]);
    }
  }, []);

  const runSync = useCallback(async () => {
    setIsSyncing(true);
    try {
      const ops = await dequeue();
      setPendingCount(ops.length);
      if (ops.length === 0) return;

      let succeeded = 0;
      let failed = 0;
      let conflicted = 0;

      for (const op of ops) {
        try {
          if (await isSnapshotStale(op)) {
            await markConflicted(op, "stale-snapshot");
            conflicted++;
            setPendingCount((prev) => Math.max(0, prev - 1));
            continue;
          }

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
            succeeded++;
            setPendingCount((prev) => Math.max(0, prev - 1));
          } else if (response.status === 409) {
            await markConflicted(op, "server-409");
            conflicted++;
            setPendingCount((prev) => Math.max(0, prev - 1));
          } else {
            failed++;
          }
        } catch {
          // network error -- leave queued for the next sync, not a reportable failure
        }
      }

      if (conflicted > 0) await refreshConflicts();

      if (succeeded > 0) {
        toast.success("Sync complete", {
          description: `${succeeded} change${succeeded === 1 ? "" : "s"} uploaded`,
        });
      }
      if (failed > 0) {
        toast.error("Sync failed", {
          description: `${failed} change${failed === 1 ? "" : "s"} failed`,
        });
      }
    } catch {
      // IndexedDB unavailable
    } finally {
      setIsSyncing(false);
    }
  }, [refreshConflicts]);

  const resolveConflict = useCallback(
    async (id: string, resolution: "keep-local" | "discard-local") => {
      await resolveConflictOp(id, resolution);
      await refreshConflicts();
      await refreshPendingCount();
    },
    [refreshConflicts, refreshPendingCount]
  );

  useEffect(() => {
    refreshPendingCount();
    refreshConflicts();
  }, [refreshPendingCount, refreshConflicts]);

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

  return { isOnline, pendingCount, isSyncing, conflicts, resolveConflict };
}
