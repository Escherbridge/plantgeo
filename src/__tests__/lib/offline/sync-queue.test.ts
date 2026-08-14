import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * No fake-indexeddb devDependency in this repo, so the persistence layer
 * (`@/lib/offline/indexed-db`) is mocked with an in-memory Map keyed by store name -- the
 * same "mock idb" approach `tile-cache.test.ts` uses for the browser APIs it touches.
 * `vi.hoisted` is required because the mock factory below and this file's own `beforeEach`
 * both need to reach the same backing store.
 */
const mockStores = vi.hoisted(() => new Map<string, Map<string, unknown>>());

function storeFor(name: string) {
  if (!mockStores.has(name)) mockStores.set(name, new Map());
  return mockStores.get(name)!;
}

vi.mock("@/lib/offline/indexed-db", () => ({
  openDB: vi.fn(async (_name: string, _version: number, stores: string[]) => {
    for (const s of stores) storeFor(s);
    return {} as IDBDatabase;
  }),
  get: vi.fn(async (_db: unknown, store: string, key: string) => storeFor(store).get(key)),
  put: vi.fn(async (_db: unknown, store: string, key: string, value: unknown) => {
    storeFor(store).set(key, { id: key, ...(value as object) });
  }),
  getAll: vi.fn(async (_db: unknown, store: string) => Array.from(storeFor(store).values())),
  deleteRecord: vi.fn(async (_db: unknown, store: string, key: string) => {
    storeFor(store).delete(key);
  }),
}));

import {
  enqueue,
  dequeue,
  remove,
  markConflicted,
  getConflicts,
  resolveConflict,
  type SyncOperation,
} from "@/lib/offline/sync-queue";

function makeOp(overrides: Partial<SyncOperation> = {}): SyncOperation {
  return {
    id: "op-1",
    type: "update",
    resource: "vegetation",
    data: { id: "res-1", value: 42 },
    timestamp: Date.now(),
    updatedAt: Date.now(),
    ...overrides,
  };
}

describe("sync-queue", () => {
  beforeEach(() => {
    mockStores.clear();
  });

  afterEach(() => {
    mockStores.clear();
  });

  it("enqueues and dequeues an operation", async () => {
    const op = makeOp();
    await enqueue(op);

    const ops = await dequeue();
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ id: "op-1", resource: "vegetation" });
  });

  it("removes an operation by id", async () => {
    await enqueue(makeOp({ id: "op-1" }));
    await enqueue(makeOp({ id: "op-2" }));

    await remove("op-1");

    const ops = await dequeue();
    expect(ops.map((o) => o.id)).toEqual(["op-2"]);
  });

  it("returns an empty array when nothing is queued", async () => {
    expect(await dequeue()).toEqual([]);
  });

  it("marks an operation conflicted: removed from the queue, filed as a conflict", async () => {
    const op = makeOp({ id: "op-1" });
    await enqueue(op);

    await markConflicted(op, "server-409");

    expect(await dequeue()).toEqual([]);
    const conflicts = await getConflicts();
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ id: "op-1", conflictReason: "server-409" });
    expect(typeof conflicts[0].detectedAt).toBe("number");
  });

  it("records a stale-snapshot conflict distinctly from a server 409", async () => {
    const op = makeOp({ id: "op-1" });
    await markConflicted(op, "stale-snapshot");

    const [conflict] = await getConflicts();
    expect(conflict.conflictReason).toBe("stale-snapshot");
  });

  it("keep-local re-enqueues the op with a fresh snapshot and clears the conflict", async () => {
    const op = makeOp({ id: "op-1", updatedAt: 100 });
    await markConflicted(op, "server-409");

    await resolveConflict("op-1", "keep-local");

    expect(await getConflicts()).toEqual([]);
    const [requeued] = await dequeue();
    expect(requeued).toMatchObject({ id: "op-1", resource: "vegetation" });
    expect(requeued.updatedAt).toBeGreaterThan(100);
  });

  it("discard-local clears the conflict without re-queuing", async () => {
    const op = makeOp({ id: "op-1" });
    await markConflicted(op, "server-409");

    await resolveConflict("op-1", "discard-local");

    expect(await getConflicts()).toEqual([]);
    expect(await dequeue()).toEqual([]);
  });

  it("resolving a conflict that no longer exists is a no-op", async () => {
    await expect(resolveConflict("missing", "keep-local")).resolves.toBeUndefined();
    expect(await dequeue()).toEqual([]);
  });
});
