import { openDB, getAll, put, deleteRecord } from "./indexed-db";

export type SyncOperation = {
  id: string;
  type: "create" | "update" | "delete";
  resource: string;
  data: unknown;
  timestamp: number;
  /** The local snapshot's version at enqueue time; compared against the server's current
   *  `updatedAt` on replay to detect an edit that happened while this one sat queued. */
  updatedAt: number;
};

export type ConflictReason = "server-409" | "stale-snapshot";

export type ConflictedOperation = SyncOperation & {
  conflictReason: ConflictReason;
  detectedAt: number;
};

const DB_NAME = "plantgeo-offline";
// v2 adds the conflicts store; bumping the version is what makes onupgradeneeded create it
// for a client that already has a v1 database on disk.
const DB_VERSION = 2;
const STORE = "sync-queue";
const CONFLICT_STORE = "sync-conflicts";

let dbPromise: Promise<IDBDatabase> | null = null;

function getDB(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, [STORE, CONFLICT_STORE]);
  }
  return dbPromise;
}

export async function enqueue(op: SyncOperation): Promise<void> {
  const db = await getDB();
  await put(db, STORE, op.id, op);
}

export async function dequeue(): Promise<SyncOperation[]> {
  const db = await getDB();
  return getAll<SyncOperation>(db, STORE);
}

export async function remove(id: string): Promise<void> {
  const db = await getDB();
  await deleteRecord(db, STORE, id);
}

/** Pulls an op out of the active queue and files it as a conflict awaiting user resolution. */
export async function markConflicted(op: SyncOperation, reason: ConflictReason): Promise<void> {
  const db = await getDB();
  await deleteRecord(db, STORE, op.id);
  const conflicted: ConflictedOperation = {
    ...op,
    conflictReason: reason,
    detectedAt: Date.now(),
  };
  await put(db, CONFLICT_STORE, conflicted.id, conflicted);
}

export async function getConflicts(): Promise<ConflictedOperation[]> {
  const db = await getDB();
  return getAll<ConflictedOperation>(db, CONFLICT_STORE);
}

/**
 * Keep-local re-submits the op on the next sync pass with a fresh snapshot version;
 * discard-local drops it. Either way the conflict record is cleared.
 */
export async function resolveConflict(
  id: string,
  resolution: "keep-local" | "discard-local"
): Promise<void> {
  const db = await getDB();
  const conflicts = await getAll<ConflictedOperation>(db, CONFLICT_STORE);
  const conflict = conflicts.find((c) => c.id === id);
  await deleteRecord(db, CONFLICT_STORE, id);

  if (resolution === "keep-local" && conflict) {
    await enqueue({
      id: conflict.id,
      type: conflict.type,
      resource: conflict.resource,
      data: conflict.data,
      timestamp: conflict.timestamp,
      updatedAt: Date.now(),
    });
  }
}
