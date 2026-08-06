/**
 * A react-query v5 `persister` (see `defaultOptions.queries.persister` in
 * src/lib/providers.tsx) that backs an ALLOWLISTED subset of queries with IndexedDB, so
 * scrubbing the time slider back to a previously-viewed day is instant and survives a
 * reload. Everything else passes straight through to the real `queryFn`, untouched.
 *
 * See src/lib/cache/AGENTS.md for the full rationale: the allowlist rule, the TTL policy,
 * the storage budget, and the degradation matrix.
 */
import type { QueryPersister } from "@tanstack/react-query";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import {
  deleteEntry,
  getAllEntries,
  getEntry,
  isIndexedDbAvailable,
  setEntry,
  type IndexedDbStoreConfig,
} from "./indexeddb-store";

/** Exported for tests only -- production callers never need the database/store names directly. */
export const STORE_CONFIG: IndexedDbStoreConfig = {
  databaseName: "plantgeo-query-cache",
  storeName: "layer-query-entries",
};

/** Bump when `StoredLayerQueryEntry`'s shape or meaning changes; old entries become misses. */
export const CACHE_SCHEMA_VERSION = 1;

/** A past calendar day's observations are immutable once published. */
export const HISTORICAL_TTL_MS = 30 * 24 * 60 * 60 * 1000;

/** "Today" (or a date we cannot yet prove is in the past) keeps accumulating observations. */
export const LIVE_TTL_MS = 5 * 60 * 1000;

/** Soft cap on total cache size; see AGENTS.md "storage budget" for the measurement behind it. */
export const MAX_TOTAL_CACHE_BYTES = 50 * 1024 * 1024;

/** One persisted query result, plus enough metadata to expire and evict it. */
export interface StoredLayerQueryEntry<T = unknown> {
  /** Equal to the IDB key (the query's `queryHash`); carried on the value for iteration/eviction. */
  key: string;
  schemaVersion: number;
  createdAt: number;
  expiresAt: number;
  /** Bumped on every hit; the eviction sweep below drops the smallest of these first. */
  lastAccessedAt: number;
  approxByteSize: number;
  value: T;
}

/**
 * Opt-in allowlist of read-only geospatial layer reads keyed by a bbox and/or a date.
 * Nothing user-scoped, authenticated, or mutation-shaped belongs here -- this predicate
 * exists specifically so it is never accidentally satisfied by such a query.
 *
 * To cache a future layer, add its dot-joined tRPC path below. Nothing else needs to
 * change: this file has no knowledge of any router or call site, by design.
 */
const CACHEABLE_LAYER_QUERIES: readonly string[] = [
  "environmental.getStreamflow",
  "environmental.getGroundwater",
  "environmental.getVegetationIndex",
  "environmental.getDroughtClassification",
  // The isobands are the most expensive answer here to recompute and the cheapest to store:
  // a whole-PNW coarse view is at most nine features, and the archive day behind it is
  // immutable, so scrubbing back to a day already seen must never re-run the aggregation.
  "environmental.getSoilMoisture",
  "wildfire.getWeatherForBbox",
];

/** The tRPC router path a react-query key was built from, or `null` when it doesn't look like one. */
function routerPathFromQueryKey(queryKey: readonly unknown[]): string | null {
  const segments = queryKey[0];
  if (!Array.isArray(segments) || segments.length === 0) return null;
  if (!segments.every((segment) => typeof segment === "string")) return null;
  return segments.join(".");
}

/** The procedure's input object, as `@trpc/react-query` shapes a query key's second element. */
function queryInputRecord(queryKey: readonly unknown[]): Record<string, unknown> | null {
  const second = queryKey[1];
  if (typeof second !== "object" || second === null) return null;
  const input = (second as { input?: unknown }).input;
  return typeof input === "object" && input !== null ? (input as Record<string, unknown>) : null;
}

/**
 * True only for an allowlisted read whose input actually carries a bbox and/or a date.
 * Opts IN explicitly; there is no default-cache path.
 */
export function isPersistableQueryKey(queryKey: readonly unknown[]): boolean {
  const routerPath = routerPathFromQueryKey(queryKey);
  if (routerPath === null || !CACHEABLE_LAYER_QUERIES.includes(routerPath)) return false;
  const input = queryInputRecord(queryKey);
  if (input === null) return false;
  const hasBbox = typeof input.bbox === "string" && input.bbox.length > 0;
  const hasDate = typeof input.date === "string" && input.date.length > 0;
  return hasBbox || hasDate;
}

/**
 * Historical days cache for HISTORICAL_TTL_MS; "today" (or later, or a date we cannot yet
 * prove is in the past) caches only for LIVE_TTL_MS. "Today" is always the server's
 * `serverCurrentDate` from useTimeSliderStore -- this deliberately never reads the browser
 * clock to decide which calendar date is live; see src/stores/time-slider-store.ts.
 */
export function resolveCacheTtlMs(queryKey: readonly unknown[]): number {
  const input = queryInputRecord(queryKey);
  const date = input && typeof input.date === "string" ? input.date : null;
  if (date === null) return LIVE_TTL_MS;
  const serverCurrentDate = useTimeSliderStore.getState().capabilities?.serverCurrentDate ?? null;
  if (serverCurrentDate === null) return LIVE_TTL_MS;
  return date < serverCurrentDate ? HISTORICAL_TTL_MS : LIVE_TTL_MS;
}

function isStoredLayerQueryEntry(value: unknown): value is StoredLayerQueryEntry {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.key === "string" &&
    typeof candidate.schemaVersion === "number" &&
    typeof candidate.createdAt === "number" &&
    typeof candidate.expiresAt === "number" &&
    typeof candidate.lastAccessedAt === "number" &&
    typeof candidate.approxByteSize === "number" &&
    "value" in candidate
  );
}

/** Fresh means the right schema AND not yet past its TTL; anything else is treated as a miss. */
function isEntryFresh(entry: StoredLayerQueryEntry): boolean {
  return entry.schemaVersion === CACHE_SCHEMA_VERSION && Date.now() < entry.expiresAt;
}

/**
 * Excludes only responses that are not a real answer. Every payload this predicate ever
 * sees uses the availability/reason vocabulary in src/types/time-slider.ts, where the
 * server only ever emits a positive claim ("published", "not_published", ...) --
 * `"request_failed"` is documented CLIENT-ONLY and the server never emits it. Checking for
 * it anyway costs nothing, and this cache must never be the thing that makes a transient
 * failure look like a permanent published answer.
 */
function isCacheableResult(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "object") {
    const availability = (value as Record<string, unknown>).availability;
    if (availability === "request_failed") return false;
  }
  return true;
}

/** Rough serialized byte size, used only to weigh entries against the storage budget. */
function estimateByteSize(value: unknown): number {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).length;
  } catch {
    return 0;
  }
}

/**
 * `navigator.storage.estimate()` is a sanity check, never a dependency: when it reports
 * this origin already near its browser-assigned quota, halve the working budget for this
 * write so the cache backs off instead of being the thing that tips the origin over.
 */
async function effectiveBudgetBytes(): Promise<number> {
  const storage = typeof navigator === "undefined" ? undefined : navigator.storage;
  if (!storage?.estimate) return MAX_TOTAL_CACHE_BYTES;
  try {
    const { usage, quota } = await storage.estimate();
    if (typeof usage === "number" && typeof quota === "number" && quota > 0 && usage / quota > 0.8) {
      return MAX_TOTAL_CACHE_BYTES / 2;
    }
  } catch {
    // Best-effort signal only; fall back to the static budget below.
  }
  return MAX_TOTAL_CACHE_BYTES;
}

/** Evicts least-recently-used entries until `incomingBytes` more would fit under budget. */
async function evictLeastRecentlyUsedToFit(incomingBytes: number): Promise<void> {
  try {
    const budget = await effectiveBudgetBytes();
    const rawEntries = await getAllEntries<unknown>(STORE_CONFIG);
    const entries = rawEntries.filter(isStoredLayerQueryEntry);
    let total = entries.reduce((sum, entry) => sum + entry.approxByteSize, 0) + incomingBytes;
    if (total <= budget) return;
    const oldestFirst = [...entries].sort((a, b) => a.lastAccessedAt - b.lastAccessedAt);
    for (const entry of oldestFirst) {
      if (total <= budget) break;
      await deleteEntry(STORE_CONFIG, entry.key);
      total -= entry.approxByteSize;
    }
  } catch {
    // Eviction is best-effort: worst case the next write lands slightly over budget.
  }
}

/**
 * The persister wired into `defaultOptions.queries.persister`. For any query outside the
 * allowlist -- or when IndexedDB is unavailable at all -- this is a pure passthrough to
 * `queryFn`, with zero extra work.
 *
 * For an allowlisted query: a fresh cache hit returns the stored value immediately (the
 * LRU touch-write happens in the background, never delaying the return). A miss, an
 * expired entry, a schema-version mismatch, or a corrupt/unreadable entry all fall through
 * to `queryFn` identically -- the caller can't tell them apart, by design.
 */
export const indexedDbLayerQueryPersister: QueryPersister = async (queryFn, context, query) => {
  const queryKey = query.queryKey as readonly unknown[];
  if (!isIndexedDbAvailable() || !isPersistableQueryKey(queryKey)) {
    return queryFn(context);
  }

  const cacheKey = query.queryHash;

  try {
    const stored = await getEntry<unknown>(STORE_CONFIG, cacheKey);
    if (stored !== null && isStoredLayerQueryEntry(stored)) {
      if (isEntryFresh(stored)) {
        // Fire-and-forget: bumping recency must never delay returning the cached value.
        void setEntry(STORE_CONFIG, cacheKey, { ...stored, lastAccessedAt: Date.now() }).catch(
          () => {}
        );
        return stored.value;
      }
      void deleteEntry(STORE_CONFIG, cacheKey).catch(() => {});
    } else if (stored !== null) {
      // Shape didn't match StoredLayerQueryEntry at all -- a corrupt entry. Drop it.
      void deleteEntry(STORE_CONFIG, cacheKey).catch(() => {});
    }
  } catch {
    // Any read/parse failure is a miss, never a thrown error.
  }

  const result = await queryFn(context);

  try {
    if (isCacheableResult(result)) {
      const now = Date.now();
      const approxByteSize = estimateByteSize(result);
      const entry: StoredLayerQueryEntry = {
        key: cacheKey,
        schemaVersion: CACHE_SCHEMA_VERSION,
        createdAt: now,
        expiresAt: now + resolveCacheTtlMs(queryKey),
        lastAccessedAt: now,
        approxByteSize,
        value: result,
      };
      await evictLeastRecentlyUsedToFit(approxByteSize);
      await setEntry(STORE_CONFIG, cacheKey, entry);
    }
  } catch {
    // Writing to the cache is best-effort; `result` below is still the real answer.
  }

  return result;
};
