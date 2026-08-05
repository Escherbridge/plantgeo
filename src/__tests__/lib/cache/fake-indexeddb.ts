/**
 * Minimal in-memory stand-in for the browser's IndexedDB -- just deep enough to exercise
 * src/lib/cache/indexeddb-store.ts: open-with-upgrade, one object store, get/put/delete/getAll.
 * Not a spec-complete polyfill. jsdom (this repo's vitest environment) does not implement
 * IndexedDB at all, so tests that need a real read/write round trip install this on
 * `globalThis.indexedDB` for the duration of the test. See src/lib/cache/AGENTS.md for why
 * the production wrapper itself is hand-rolled rather than a dependency -- this test double
 * is hand-rolled for the same reason.
 *
 * Each database name keeps its own persistent `Map`, so calling `open()` again after a
 * `close()` -- exactly what happens between two unrelated calls in the real wrapper, and
 * what simulates a page reload in a test -- still sees previously written rows.
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

class FakeObjectStore {
  constructor(private readonly table: Map<string, unknown>) {}

  get(key: string): FakeRequest<unknown> {
    const request = new FakeRequest<unknown>();
    request.resolve(this.table.get(key));
    return request;
  }

  put(value: unknown, key: string): FakeRequest<string> {
    const request = new FakeRequest<string>();
    this.table.set(key, value);
    request.resolve(key);
    return request;
  }

  delete(key: string): FakeRequest<undefined> {
    const request = new FakeRequest<undefined>();
    this.table.delete(key);
    request.resolve(undefined);
    return request;
  }

  getAll(): FakeRequest<unknown[]> {
    const request = new FakeRequest<unknown[]>();
    request.resolve([...this.table.values()]);
    return request;
  }
}

class FakeTransaction {
  oncomplete: Listener = null;
  onerror: Listener = null;
  onabort: Listener = null;

  constructor(private readonly stores: Map<string, Map<string, unknown>>) {
    queueMicrotask(() => queueMicrotask(() => this.oncomplete?.()));
  }

  objectStore(name: string): FakeObjectStore {
    let table = this.stores.get(name);
    if (!table) {
      table = new Map();
      this.stores.set(name, table);
    }
    return new FakeObjectStore(table);
  }
}

class FakeDatabase {
  readonly objectStoreNames = { contains: (name: string) => this.stores.has(name) };

  constructor(private readonly stores: Map<string, Map<string, unknown>>) {}

  createObjectStore(name: string): void {
    if (!this.stores.has(name)) this.stores.set(name, new Map());
  }

  transaction(_name: string, _mode: string): FakeTransaction {
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

/** A fresh fake `indexedDB`. Assign to `globalThis.indexedDB` for the duration of a test. */
export function createFakeIndexedDb() {
  const databases = new Map<string, Map<string, Map<string, unknown>>>();

  return {
    open(name: string): FakeOpenRequest {
      const request = new FakeOpenRequest();
      const isNewDatabase = !databases.has(name);
      const stores = databases.get(name) ?? new Map<string, Map<string, unknown>>();
      databases.set(name, stores);
      request.result = new FakeDatabase(stores);
      queueMicrotask(() => {
        if (isNewDatabase) request.onupgradeneeded?.();
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
