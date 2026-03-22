import { openDB, getAll, put, deleteRecord } from "./indexed-db";

export type SyncOperation = {
  id: string;
  type: "create" | "update" | "delete";
  resource: string;
  data: unknown;
  timestamp: number;
};

const DB_NAME = "plantgeo-offline";
const DB_VERSION = 1;
const STORE = "sync-queue";

let dbPromise: Promise<IDBDatabase> | null = null;

function getDB(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, [STORE]);
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
