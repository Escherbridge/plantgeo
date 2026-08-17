/**
 * Minimal in-memory stand-in for the browser's IndexedDB -- just deep enough to exercise
 * src/lib/cache/indexeddb-store.ts: open-with-upgrade, one object store, get/put/delete/getAll,
 * and a forward-only cursor. Not a spec-complete polyfill. jsdom (this repo's vitest environment)
 * does not implement IndexedDB at all, so tests that need a real read/write round trip install
 * this on `globalThis.indexedDB` for the duration of the test. See src/lib/cache/AGENTS.md for
 * why the production wrapper itself is hand-rolled rather than a dependency -- this test double
 * is hand-rolled for the same reason.
 *
 * Each database name keeps its own persistent `Map`, so calling `open()` again after a
 * `close()` -- exactly what happens between two unrelated calls in the real wrapper, and
 * what simulates a page reload in a test -- still sees previously written rows.
 *
 * A transaction fires `oncomplete` only once every request it issued has settled. That is not
 * decoration: `forEachEntry` treats `oncomplete` as "the walk is over", so a transaction that
 * completed while a cursor was still stepping would silently truncate the walk and every
 * budget/index total computed from it.
 */

type Listener = (() => void) | null;

class FakeRequest<T> {
  result: T | undefined = undefined;
  onsuccess: Listener = null;
  onerror: Listener = null;

  resolve(value: T): void {
    queueMicrotask(() => {
      this.result = value;
      this.onsuccess?.();
    });
  }
}

/** Delivers one row per `continue()`, over a snapshot taken when the cursor was opened. */
class FakeCursorRequest {
  result: { key: string; value: unknown; continue: () => void } | null = null;
  onsuccess: Listener = null;
  onerror: Listener = null;

  private index = 0;

  constructor(
    private readonly rows: Array<[string, unknown]>,
    private readonly onExhausted: () => void
  ) {
    this.step();
  }

  private step(): void {
    queueMicrotask(() => {
      if (this.index >= this.rows.length) {
        this.result = null;
        this.onsuccess?.();
        this.onExhausted();
        return;
      }
      const [key, value] = this.rows[this.index];
      this.index += 1;
      this.result = { key, value, continue: () => this.step() };
      this.onsuccess?.();
    });
  }
}

class FakeObjectStore {
  constructor(
    private readonly table: Map<string, unknown>,
    private readonly transaction: FakeTransaction
  ) {}

  get(key: string): FakeRequest<unknown> {
    const request = new FakeRequest<unknown>();
    this.transaction.trackRequest();
    request.resolve(this.table.get(key));
    queueMicrotask(() => this.transaction.settleRequest());
    return request;
  }

  put(value: unknown, key: string): FakeRequest<string> {
    const request = new FakeRequest<string>();
    this.transaction.trackRequest();
    this.table.set(key, value);
    request.resolve(key);
    queueMicrotask(() => this.transaction.settleRequest());
    return request;
  }

  delete(key: string): FakeRequest<undefined> {
    const request = new FakeRequest<undefined>();
    this.transaction.trackRequest();
    this.table.delete(key);
    request.resolve(undefined);
    queueMicrotask(() => this.transaction.settleRequest());
    return request;
  }

  getAll(): FakeRequest<unknown[]> {
    const request = new FakeRequest<unknown[]>();
    this.transaction.trackRequest();
    request.resolve([...this.table.values()]);
    queueMicrotask(() => this.transaction.settleRequest());
    return request;
  }

  getAllKeys(): FakeRequest<string[]> {
    const request = new FakeRequest<string[]>();
    this.transaction.trackRequest();
    request.resolve([...this.table.keys()]);
    queueMicrotask(() => this.transaction.settleRequest());
    return request;
  }

  openCursor(): FakeCursorRequest {
    this.transaction.trackRequest();
    return new FakeCursorRequest([...this.table.entries()], () =>
      this.transaction.settleRequest()
    );
  }
}

class FakeTransaction {
  oncomplete: Listener = null;
  onerror: Listener = null;
  onabort: Listener = null;

  private outstandingRequests = 0;
  private completed = false;

  constructor(private readonly stores: Map<string, Map<string, unknown>>) {
    // A transaction that never issues a request still completes, one tick later.
    queueMicrotask(() => queueMicrotask(() => this.completeWhenIdle()));
  }

  trackRequest(): void {
    this.outstandingRequests += 1;
  }

  settleRequest(): void {
    this.outstandingRequests -= 1;
    queueMicrotask(() => this.completeWhenIdle());
  }

  objectStore(name: string): FakeObjectStore {
    let table = this.stores.get(name);
    if (!table) {
      table = new Map();
      this.stores.set(name, table);
    }
    return new FakeObjectStore(table, this);
  }

  private completeWhenIdle(): void {
    if (this.completed || this.outstandingRequests > 0) return;
    this.completed = true;
    this.oncomplete?.();
  }
}

class FakeDatabase {
  readonly objectStoreNames = { contains: (name: string) => this.stores.has(name) };
  /** Assigned by the wrapper so a stale connection cannot block another tab's upgrade. */
  onversionchange: Listener = null;

  constructor(private readonly stores: Map<string, Map<string, unknown>>) {}

  createObjectStore(name: string): void {
    if (!this.stores.has(name)) this.stores.set(name, new Map());
  }

  /** Accepts one store name or several, matching the two-store writes the wrapper issues. */
  transaction(_names: string | string[], _mode: string): FakeTransaction {
    return new FakeTransaction(this.stores);
  }

  close(): void {}
}

class FakeOpenRequest {
  result: FakeDatabase | undefined;
  onupgradeneeded: Listener = null;
  onsuccess: Listener = null;
  onerror: Listener = null;
  onblocked: Listener = null;
}

/** One database: its stores, and the version last opened, so an upgrade fires exactly once. */
interface FakeDatabaseRecord {
  version: number;
  stores: Map<string, Map<string, unknown>>;
}

/** A fresh fake `indexedDB`. Assign to `globalThis.indexedDB` for the duration of a test. */
export function createFakeIndexedDb() {
  const databases = new Map<string, FakeDatabaseRecord>();

  return {
    open(name: string, version?: number): FakeOpenRequest {
      const request = new FakeOpenRequest();
      const existing = databases.get(name);
      const record: FakeDatabaseRecord = existing ?? { version: 0, stores: new Map() };
      databases.set(name, record);
      const requested = version ?? Math.max(record.version, 1);
      const needsUpgrade = requested > record.version;
      request.result = new FakeDatabase(record.stores);
      queueMicrotask(() => {
        if (needsUpgrade) {
          record.version = requested;
          request.onupgradeneeded?.();
        }
        queueMicrotask(() => request.onsuccess?.());
      });
      return request;
    },
  };
}

/** A fake `indexedDB.open` that always fails, for exercising the "blocked/absent" fallback. */
export function createAlwaysFailingIndexedDb() {
  return {
    open(_name: string): FakeOpenRequest {
      const request = new FakeOpenRequest();
      queueMicrotask(() => request.onerror?.());
      return request;
    },
  };
}
