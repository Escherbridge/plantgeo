export function openDB(
  name: string,
  version: number,
  stores: string[]
): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, version);

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      for (const store of stores) {
        if (!db.objectStoreNames.contains(store)) {
          db.createObjectStore(store, { keyPath: "id" });
        }
      }
    };

    request.onsuccess = (event) => {
      resolve((event.target as IDBOpenDBRequest).result);
    };

    request.onerror = (event) => {
      reject((event.target as IDBOpenDBRequest).error);
    };
  });
}

export function get<T>(
  db: IDBDatabase,
  store: string,
  key: string
): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly");
    const request = tx.objectStore(store).get(key);

    request.onsuccess = (event) => {
      resolve((event.target as IDBRequest<T>).result ?? undefined);
    };

    request.onerror = (event) => {
      reject((event.target as IDBRequest).error);
    };
  });
}

export function put(
  db: IDBDatabase,
  store: string,
  key: string,
  value: unknown
): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    const request = tx.objectStore(store).put({ id: key, ...((value as object) ?? {}) });

    request.onsuccess = () => {
      resolve();
    };

    request.onerror = (event) => {
      reject((event.target as IDBRequest).error);
    };
  });
}

export function getAll<T>(db: IDBDatabase, store: string): Promise<T[]> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly");
    const request = tx.objectStore(store).getAll();

    request.onsuccess = (event) => {
      resolve((event.target as IDBRequest<T[]>).result);
    };

    request.onerror = (event) => {
      reject((event.target as IDBRequest).error);
    };
  });
}

export function deleteRecord(
  db: IDBDatabase,
  store: string,
  key: string
): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    const request = tx.objectStore(store).delete(key);

    request.onsuccess = () => {
      resolve();
    };

    request.onerror = (event) => {
      reject((event.target as IDBRequest).error);
    };
  });
}
