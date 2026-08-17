/**
 * A react-query v5 `persister` (see `defaultOptions.queries.persister` in
 * src/lib/providers.tsx) that backs an ALLOWLISTED subset of queries with IndexedDB, so
 * scrubbing a layer's time slider back to a previously-viewed day is instant and survives a
 * reload. Everything else passes straight through to the real `queryFn`, untouched.
 *
 * Untouched by per-layer dates (2026-08-09), and worth stating because it looks like it should
 * not be: entries are keyed by the tRPC `queryHash`, which already embeds whatever `date` the
 * caller sent, so one layer's day hits or misses exactly as the old map-wide day did. The only
 * thing this file reads from the time-slider store is `serverCurrentDate` -- a property of the
 * server, not of any layer -- so nothing here has to know which row asked.
 *
 * Since 2026-08-16 it also STAMPS each entry with the layer and day it answers for, keeps a
 * sibling metadata row per payload so sweeps never deserialize one, and exposes the enumeration
 * (`scanSyncedDays`) and reset (`clearLayerEntries`) the per-layer "synced days" track is built
 * on. See src/lib/cache/AGENTS.md for the full rationale: the allowlist rule, the TTL policy,
 * layer/day attribution, the metadata store, the storage budget, and the degradation matrix.
 */
import type { QueryClient, QueryPersister } from "@tanstack/react-query";
import {
  DEFAULT_CLIMATE_FIELD_SIGNAL,
  climateFieldToggleId,
  isClimateFieldSignal,
} from "@/lib/environmental/climate-field";
import {
  SOIL_FIELD_MEASURES,
  isSoilFieldMeasure,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import {
  deleteEntries,
  deleteEntry,
  forEachEntry,
  getAllEntryKeys,
  getAllMetadata,
  getEntry,
  isIndexedDbAvailable,
  putEntryWithMetadata,
  putManyMetadata,
  putMetadata,
  type IndexedDbStoreConfig,
} from "./indexeddb-store";

/** Exported for tests only -- production callers never need the database/store names directly. */
export const STORE_CONFIG: IndexedDbStoreConfig = {
  databaseName: "plantgeo-query-cache",
  storeName: "layer-query-entries",
  metadataStoreName: "layer-query-metadata",
  // v2 added the metadata store; v1 databases are upgraded in place, payloads untouched.
  version: 2,
};

/** Bump when `StoredLayerQueryEntry`'s VALUE shape or meaning changes; old entries become misses. */
export const CACHE_SCHEMA_VERSION = 1;

/**
 * How long a past calendar day's answer is served from disk before the cache stops trusting it.
 *
 * NOT an immutability claim, and it must not be read as one: this warehouse rewrites historical
 * days. The agri gap-reopen lane reopens a window and republishes it (see
 * `services/agri-data-service/.../sql/ingest/reopen_gap_windows.sql` and commit c14e36b, which
 * caps that at five generations precisely because it recurs), and the USDM/ERA5 lanes backfill.
 * What makes 30 days safe is not immutability but the background revalidation below, which runs
 * for historical days too and rewrites the entry in place when the warehouse's answer has moved.
 */
export const HISTORICAL_TTL_MS = 30 * 24 * 60 * 60 * 1000;

/** "Today" (or a date we cannot yet prove is in the past) keeps accumulating observations. */
export const LIVE_TTL_MS = 5 * 60 * 1000;

/** Soft cap on total cache size; see AGENTS.md "storage budget" for the measurement behind it. */
export const MAX_TOTAL_CACHE_BYTES = 512 * 1024 * 1024;

/** Floor the learned quota ceiling may never push the budget below. */
export const MIN_CACHE_BUDGET_BYTES = 16 * 1024 * 1024;

/** Floor on how often one entry may be revalidated in the background; see AGENTS.md "revalidation". */
export const REVALIDATION_MIN_INTERVAL_MS = 60 * 1000;

/**
 * One persisted query result. Sweeps never read this -- they read `StoredEntryMetadata` --
 * so everything here exists to serve a HIT.
 */
export interface StoredLayerQueryEntry<T = unknown> {
  /** Equal to the IDB key (the query's `queryHash`); carried on the value for iteration/eviction. */
  key: string;
  schemaVersion: number;
  createdAt: number;
  expiresAt: number;
  approxByteSize: number;
  value: T;
  /**
   * The layer row this answer draws on, resolved from the router path AND its input
   * discriminator at write time. Absent on entries written before 2026-08-16, and on any
   * router path this module cannot attribute; both are recovered by parsing `key`.
   */
  layerId?: LayerToggleId;
  /** The day requested (`input.date`). Absent for a dateless "live edge" read, which has no day. */
  day?: string;
  /** When the background revalidator last ran for this entry; defaults to `createdAt`. */
  lastRevalidatedAt?: number;
  /** Legacy only: recency lives in the metadata row now, so a hit rewrites 100 bytes, not 100 KB. */
  lastAccessedAt?: number;
}

/** The small row a sweep reads. One per payload, written in the same transaction. */
export interface StoredEntryMetadata {
  key: string;
  schemaVersion: number;
  expiresAt: number;
  lastAccessedAt: number;
  approxByteSize: number;
  layerId?: LayerToggleId;
  day?: string;
}

/** What the synced-day index knows about one (layer, day) pair. */
export interface SyncedDayFacts {
  /** The latest `expiresAt` across the entries covering this day; the day is fresh until then. */
  expiresAt: number;
  /** Their summed `approxByteSize` -- what a per-layer reset would reclaim. */
  approxByteSize: number;
  /** How many entries cover this day; one per viewport it was read at. Zero means drop the day. */
  entryCount: number;
}

/** Every layer's currently-fresh days. Built by ONE metadata read, never per render. */
export type SyncedDayIndex = Map<LayerToggleId, Map<string, SyncedDayFacts>>;

/** What a scan found, and which keys it covered -- the latter dedupes a concurrent write's replay. */
export interface SyncedDayScan {
  index: SyncedDayIndex;
  coveredKeys: Set<string>;
}

/**
 * Opt-in allowlist of read-only geospatial layer reads keyed by a bbox and/or a date.
 * Nothing user-scoped, authenticated, or mutation-shaped belongs here -- this predicate
 * exists specifically so it is never accidentally satisfied by such a query.
 *
 * To cache a future layer, add its dot-joined tRPC path below AND give it an attribution in
 * `toggleIdForRouterPath`; the allowlist test asserts the two agree.
 */
export const CACHEABLE_LAYER_QUERIES: readonly string[] = [
  "environmental.getStreamflow",
  "environmental.getGroundwater",
  "environmental.getVegetationIndex",
  "environmental.getDroughtClassification",
  // The isobands are the most expensive answer here to recompute and the cheapest to store:
  // a whole-PNW coarse view is at most nine features, so scrubbing back to a day already seen
  // must not re-run the aggregation on the critical path. It is still revalidated in the
  // background afterwards -- see HISTORICAL_TTL_MS on why a past day is not settled.
  "environmental.getSoilField",
  "environmental.getClimateField",
  "environmental.getSoilSurvey",
  "environmental.getWatersheds",
  "wildfire.getWeatherForBbox",
];

/**
 * The one toggle each single-layer router path draws. `getSoilField` and `getClimateField`
 * are deliberately absent: they serve twelve toggles between them and are resolved from their
 * input discriminator instead -- see `toggleIdForRouterPath`.
 */
const TOGGLE_ID_BY_ROUTER_PATH: Readonly<Record<string, LayerToggleId>> = {
  // Gauges and wells are two feeds drawn by the ONE `water` toggle, so both attribute to it --
  // see LayerManager's note on their shared day. Not a mistake, and not a collision: a day is
  // synced for `water` when either feed's answer for it is on disk.
  "environmental.getStreamflow": "water",
  "environmental.getGroundwater": "water",
  "environmental.getVegetationIndex": "vegetation",
  "environmental.getDroughtClassification": "drought",
  "environmental.getSoilSurvey": "soil-survey",
  "environmental.getWatersheds": "watersheds",
  "wildfire.getWeatherForBbox": "weather",
};

/**
 * The measure the server assumes when a caller omits it, mirrored from
 * `getPublishedSoilField` in src/lib/server/services/environmental-read-model.ts. Attributing
 * a `measure`-less entry to anything else would put the moisture answer on another row's track.
 */
const DEFAULT_SOIL_FIELD_MEASURE: SoilFieldMeasure = "moisture";

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
 * The toggle a router path draws, discriminated by input where one path serves several rows.
 *
 * `getSoilField` backs `soil-moisture`, `soil-temperature` and `soil-vpd`; `getClimateField`
 * backs nine climate rows. A flat path-to-toggle map would put all twelve on one track and
 * mislabel the majority of a real cache -- soil-field entries alone are the largest group in
 * it. Both discriminators are resolved through the shared vocabulary tables rather than a
 * local list, so a tenth climate signal or a fourth soil measure is attributed with no change
 * here; and an omitted discriminator resolves to the same default the SERVER resolves it to,
 * because that is the answer the entry actually holds.
 */
function toggleIdForRouterPath(
  routerPath: string,
  input: Record<string, unknown>
): LayerToggleId | null {
  if (routerPath === "environmental.getSoilField") {
    const requested = input.measure;
    const measure =
      typeof requested === "string" && isSoilFieldMeasure(requested)
        ? requested
        : DEFAULT_SOIL_FIELD_MEASURE;
    return SOIL_FIELD_MEASURES[measure].toggleId;
  }
  if (routerPath === "environmental.getClimateField") {
    const requested = input.signal;
    const signal =
      typeof requested === "string" && isClimateFieldSignal(requested)
        ? requested
        : DEFAULT_CLIMATE_FIELD_SIGNAL;
    return climateFieldToggleId(signal);
  }
  return TOGGLE_ID_BY_ROUTER_PATH[routerPath] ?? null;
}

/** The layer row and requested day a query key describes; either may be absent. */
export interface QueryKeyAttribution {
  layerId: LayerToggleId | null;
  /** `input.date`, or undefined for a read of the live edge, which names no day. */
  day: string | undefined;
}

/** Attribution for a react-query key. Exported for the test that keeps it exhaustive. */
export function attributeQueryKey(queryKey: readonly unknown[]): QueryKeyAttribution {
  const routerPath = routerPathFromQueryKey(queryKey);
  if (routerPath === null) return { layerId: null, day: undefined };
  const input = queryInputRecord(queryKey) ?? {};
  const day = typeof input.date === "string" && input.date.length > 0 ? input.date : undefined;
  return { layerId: toggleIdForRouterPath(routerPath, input), day };
}

/**
 * Attribution recovered from the IDB key alone, for entries written before the stamp existed.
 *
 * react-query's default `hashKey` is `JSON.stringify` with sorted object keys, so a key parses
 * straight back into the query key it was built from -- verified against 504 live production
 * entries. That is why the stamp needed no `CACHE_SCHEMA_VERSION` bump: the fallback attributes
 * legacy rows exactly, so nothing had to be discarded to make the index honest. It is a FALLBACK
 * and not the primary path because the format belongs to react-query, not to us: a future change
 * to how it hashes keys would silently strand every unstamped row, and the stamp would not care.
 */
function attributionFromCacheKey(cacheKey: string): QueryKeyAttribution {
  try {
    const parsed: unknown = JSON.parse(cacheKey);
    if (!Array.isArray(parsed)) return { layerId: null, day: undefined };
    return attributeQueryKey(parsed);
  } catch {
    return { layerId: null, day: undefined };
  }
}

/** A stored row's attribution: the stamp when it has one, otherwise parsed from its key. */
function attributeStoredRow(
  row: { layerId?: LayerToggleId; day?: string },
  cacheKey: string
): QueryKeyAttribution {
  if (row.layerId !== undefined) return { layerId: row.layerId, day: row.day };
  return attributionFromCacheKey(cacheKey);
}

/**
 * Historical days cache for HISTORICAL_TTL_MS; "today" (or later, or a date we cannot yet
 * prove is in the past) caches only for LIVE_TTL_MS. "Today" is always the server's
 * `serverCurrentDate` from useTimeSliderStore -- never any layer's own day, and never the
 * browser clock, which would disagree with the server across a UTC midnight; see
 * src/stores/time-slider-store.ts.
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
    "value" in candidate
  );
}

/** Fresh means the right schema AND not yet past its TTL; anything else is treated as a miss. */
function isEntryFresh(entry: { schemaVersion: number; expiresAt: number }): boolean {
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
 * Running totals, so the write path costs no scan at all.
 *
 * Both are null until one walk establishes them; every walk refreshes them, and each write
 * adjusts the byte total by its own signed delta. Without this, EVERY write paid a whole-store
 * read just to ask whether it fitted. Drift from anything that changes the store behind our back
 * (another tab, the browser reclaiming space) is bounded and corrected by the next walk -- and
 * a REFUSED write invalidates them outright, which is what stops a browser quota below our own
 * budget from wedging the cache silently.
 */
let totalCacheBytes: number | null = null;
let lastKnownBudgetBytes: number | null = null;

/**
 * The browser's real ceiling, learned from a write it actually refused rather than guessed from
 * a table of per-browser quotas. iOS Safari binds well below `MAX_TOTAL_CACHE_BYTES` and, before
 * 16.4, exposes no `navigator.storage.estimate` at all, so the 80%-of-quota backstop never fires
 * there; without this the first `QuotaExceededError` would freeze the running total and every
 * later write would fail silently for the rest of the session.
 */
let quotaCeilingBytes: number | null = null;

/** Bytes written by other callers while a walk was in flight; re-added when its total lands. */
let bytesWrittenDuringWalk = 0;
let walkInFlight = false;

/** Every full pass runs one at a time, so no pass can clobber another's total. */
let walkQueue: Promise<unknown> = Promise.resolve();

/**
 * Exported for tests only: forgets the accounting so the next write re-derives it from the store.
 * A test that swaps in a fresh fake IndexedDB has changed the store behind the accounting's back,
 * which is precisely the drift the totals cannot see.
 */
export function resetCacheAccounting(): void {
  totalCacheBytes = null;
  lastKnownBudgetBytes = null;
  quotaCeilingBytes = null;
  bytesWrittenDuringWalk = 0;
  walkInFlight = false;
}

function serializeFullPass<T>(work: () => Promise<T>): Promise<T> {
  const next = walkQueue.then(work, work);
  walkQueue = next.then(
    () => undefined,
    () => undefined
  );
  return next;
}

function recordByteDelta(delta: number): void {
  if (totalCacheBytes !== null) totalCacheBytes += delta;
  if (walkInFlight) bytesWrittenDuringWalk += delta;
}

/**
 * `navigator.storage.estimate()` is a sanity check, never a dependency: when it reports this
 * origin already near its browser-assigned quota, halve the working budget so the cache backs
 * off instead of being the thing that tips the origin over. A ceiling learned from a refused
 * write always wins over both.
 */
async function effectiveBudgetBytes(): Promise<number> {
  let budget = MAX_TOTAL_CACHE_BYTES;
  const storage = typeof navigator === "undefined" ? undefined : navigator.storage;
  if (storage?.estimate) {
    try {
      const { usage, quota } = await storage.estimate();
      if (
        typeof usage === "number" &&
        typeof quota === "number" &&
        quota > 0 &&
        usage / quota > 0.8
      ) {
        budget = MAX_TOTAL_CACHE_BYTES / 2;
      }
    } catch {
      // Best-effort signal only; the static budget stands.
    }
  }
  if (quotaCeilingBytes !== null) budget = Math.min(budget, quotaCeilingBytes);
  return Math.max(budget, MIN_CACHE_BUDGET_BYTES);
}

/** A refused write is the only honest measurement of the browser's own limit; take it. */
function learnQuotaCeilingFromRefusal(): void {
  const observed = totalCacheBytes ?? lastKnownBudgetBytes ?? MAX_TOTAL_CACHE_BYTES;
  const learned = Math.max(MIN_CACHE_BUDGET_BYTES, Math.floor(observed * 0.9));
  quotaCeilingBytes = quotaCeilingBytes === null ? learned : Math.min(quotaCeilingBytes, learned);
  totalCacheBytes = null;
  lastKnownBudgetBytes = null;
}

/** One live entry's metadata. No payload is ever deserialized to produce this. */
interface CacheEntryFacts {
  key: string;
  approxByteSize: number;
  lastAccessedAt: number;
  expiresAt: number;
  layerId: LayerToggleId | null;
  day: string | undefined;
}

interface CacheWalk {
  live: CacheEntryFacts[];
  /** Expired, wrong-schema, orphaned or unreadable rows: swept by whoever asked for the walk. */
  deadKeys: string[];
  liveBytes: number;
  /** Every payload key the walk saw, live or dead. */
  coveredKeys: Set<string>;
}

/**
 * Builds metadata rows for payloads that have none -- everything written before the metadata
 * store existed. The ONE place a payload is deserialized outside a cache hit, and it runs once
 * per key, ever.
 */
async function backfillMetadata(
  missingKeys: Set<string>,
  into: Map<string, StoredEntryMetadata>
): Promise<void> {
  const rows: Array<{ key: string; metadata: StoredEntryMetadata }> = [];
  await forEachEntry<unknown>(STORE_CONFIG, (value, key) => {
    if (!missingKeys.has(key) || !isStoredLayerQueryEntry(value)) return;
    const { layerId, day } = attributeStoredRow(value, key);
    const metadata: StoredEntryMetadata = {
      key,
      schemaVersion: value.schemaVersion,
      expiresAt: value.expiresAt,
      lastAccessedAt: value.lastAccessedAt ?? value.createdAt,
      approxByteSize: value.approxByteSize ?? estimateByteSize(value.value),
      ...(layerId === null ? {} : { layerId }),
      ...(day === undefined ? {} : { day }),
    };
    rows.push({ key, metadata });
    into.set(key, metadata);
  });
  if (rows.length > 0) await putManyMetadata(STORE_CONFIG, rows);
}

/**
 * One pass over the METADATA store -- never the payloads -- plus a key-only read of the payload
 * store to find rows the metadata does not cover yet and rows it covers that no longer exist.
 */
async function walkCache(): Promise<CacheWalk> {
  walkInFlight = true;
  bytesWrittenDuringWalk = 0;
  const payloadKeys = await getAllEntryKeys(STORE_CONFIG);
  const payloadKeySet = new Set(payloadKeys);
  const metadataRows = await getAllMetadata<StoredEntryMetadata>(STORE_CONFIG);
  const byKey = new Map<string, StoredEntryMetadata>();
  for (const row of metadataRows) {
    if (row !== null && typeof row === "object" && typeof row.key === "string") {
      byKey.set(row.key, row);
    }
  }

  const deadKeys: string[] = [];
  // Metadata whose payload is gone: the browser reclaimed it, or an older build deleted it.
  for (const key of byKey.keys()) {
    if (!payloadKeySet.has(key)) deadKeys.push(key);
  }
  const unindexed = payloadKeys.filter((key) => !byKey.has(key));
  if (unindexed.length > 0) await backfillMetadata(new Set(unindexed), byKey);

  const live: CacheEntryFacts[] = [];
  let liveBytes = 0;
  for (const [key, row] of byKey) {
    if (!payloadKeySet.has(key)) continue;
    if (typeof row.expiresAt !== "number" || !isEntryFresh(row)) {
      deadKeys.push(key);
      continue;
    }
    const { layerId, day } = attributeStoredRow(row, key);
    live.push({
      key,
      approxByteSize: typeof row.approxByteSize === "number" ? row.approxByteSize : 0,
      lastAccessedAt: typeof row.lastAccessedAt === "number" ? row.lastAccessedAt : 0,
      expiresAt: row.expiresAt,
      layerId,
      day,
    });
    liveBytes += typeof row.approxByteSize === "number" ? row.approxByteSize : 0;
  }
  // A payload with no metadata row that the backfill could not read is unreadable: sweep it.
  for (const key of payloadKeys) {
    if (!byKey.has(key)) deadKeys.push(key);
  }
  return { live, deadKeys, liveBytes, coveredKeys: payloadKeySet };
}

/** Applies a walk's total, re-adding whatever landed while it was in flight. */
function commitWalkTotal(liveBytes: number, committed: boolean): void {
  totalCacheBytes = committed ? liveBytes + bytesWrittenDuringWalk : null;
  bytesWrittenDuringWalk = 0;
  walkInFlight = false;
}

/** Folds walked metadata into the per-layer, per-day index the sync track reads. */
function buildSyncedDayIndex(records: readonly CacheEntryFacts[]): SyncedDayIndex {
  const index: SyncedDayIndex = new Map();
  for (const record of records) {
    if (record.layerId === null || record.day === undefined) continue;
    let days = index.get(record.layerId);
    if (days === undefined) {
      days = new Map();
      index.set(record.layerId, days);
    }
    const existing = days.get(record.day);
    days.set(record.day, {
      expiresAt: Math.max(existing?.expiresAt ?? 0, record.expiresAt),
      approxByteSize: (existing?.approxByteSize ?? 0) + record.approxByteSize,
      entryCount: (existing?.entryCount ?? 0) + 1,
    });
  }
  return index;
}

/** One entry landed on disk. `byteDelta` is signed; an overwrite that shrank the payload is negative. */
export interface CacheIndexEntryEvent {
  cacheKey: string;
  layerId: LayerToggleId;
  day: string;
  expiresAt: number;
  byteDelta: number;
  /** True when this key was not already on disk, so the day gained an entry rather than replacing one. */
  isNewEntry: boolean;
}

/** One entry left disk. The day survives while another viewport's entry still covers it. */
export interface CacheIndexDropEvent {
  cacheKey: string;
  layerId: LayerToggleId;
  day: string;
  approxByteSize: number;
}

/**
 * The one seam through which this module tells the synced-day index what it did.
 *
 * A callback rather than an import of the store, so the dependency runs one way only
 * (store -> cache) and the cache layer stays testable without pulling zustand in. The single
 * writer is `src/stores/sync-index-store.ts`, which registers itself before its first scan.
 */
export interface CacheIndexObserver {
  entryStored(event: CacheIndexEntryEvent): void;
  entryDropped(event: CacheIndexDropEvent): void;
  /** A full walk produced authoritative truth; replace the index with it. */
  indexRebuilt(index: SyncedDayIndex): void;
}

let cacheIndexObserver: CacheIndexObserver | null = null;

/** Registers (or with `null`, clears) the sync index's view of this cache. */
export function setCacheIndexObserver(observer: CacheIndexObserver | null): void {
  cacheIndexObserver = observer;
}

function notifyEntryStored(
  cacheKey: string,
  attribution: QueryKeyAttribution,
  expiresAt: number,
  byteDelta: number,
  isNewEntry: boolean
): void {
  if (cacheIndexObserver === null) return;
  if (attribution.layerId === null || attribution.day === undefined) return;
  try {
    cacheIndexObserver.entryStored({
      cacheKey,
      layerId: attribution.layerId,
      day: attribution.day,
      expiresAt,
      byteDelta,
      isNewEntry,
    });
  } catch {
    // A subscriber fault must never surface as a failed layer read.
  }
}

function notifyEntryDropped(
  cacheKey: string,
  attribution: QueryKeyAttribution,
  approxByteSize: number
): void {
  if (cacheIndexObserver === null) return;
  if (attribution.layerId === null || attribution.day === undefined) return;
  try {
    cacheIndexObserver.entryDropped({
      cacheKey,
      layerId: attribution.layerId,
      day: attribution.day,
      approxByteSize,
    });
  } catch {
    // As above.
  }
}

function notifyIndexRebuilt(index: SyncedDayIndex): void {
  if (cacheIndexObserver === null) return;
  try {
    cacheIndexObserver.indexRebuilt(index);
  } catch {
    // As above.
  }
}

function currentBudgetFits(incomingBytes: number): boolean {
  return (
    totalCacheBytes !== null &&
    lastKnownBudgetBytes !== null &&
    totalCacheBytes + incomingBytes <= lastKnownBudgetBytes
  );
}

/**
 * Makes room for `incomingBytes`, sweeping expired rows first and evicting least-recently-used
 * live ones only if still over budget.
 *
 * Expiry sweeping happens HERE rather than on a timer because this is the only pass that
 * already reads every metadata row; nothing else ever dropped an expired row that was not read
 * again, and a measured 47% of a real production cache was expired-but-resident, holding budget
 * against live data. Passes are serialized, so twelve layer queries mounting at once collapse
 * into one -- and the second and later of them find the budget already satisfied and return.
 */
async function ensureCapacityFor(incomingBytes: number): Promise<void> {
  if (currentBudgetFits(incomingBytes)) return;
  await serializeFullPass(async () => {
    try {
      if (currentBudgetFits(incomingBytes)) return;
      const budget = await effectiveBudgetBytes();
      lastKnownBudgetBytes = budget;
      const walk = await walkCache();
      const evictedKeys = new Set<string>();
      let total = walk.liveBytes;
      if (total + incomingBytes > budget) {
        const oldestFirst = [...walk.live].sort((a, b) => a.lastAccessedAt - b.lastAccessedAt);
        for (const record of oldestFirst) {
          if (total + incomingBytes <= budget) break;
          evictedKeys.add(record.key);
          total -= record.approxByteSize;
        }
      }
      const doomed = [...walk.deadKeys, ...evictedKeys];
      const committed = await deleteEntries(STORE_CONFIG, doomed);
      // An aborted delete leaves the store holding rows this total says are gone. Re-deriving
      // is the only honest response; a total that drifts DOWN under-evicts forever.
      commitWalkTotal(total, committed);
      if (doomed.length > 0 && committed) {
        const survivors =
          evictedKeys.size === 0 ? walk.live : walk.live.filter((r) => !evictedKeys.has(r.key));
        // Only when something actually went: republishing an unchanged index on every write
        // would re-render every subscriber for nothing.
        notifyIndexRebuilt(buildSyncedDayIndex(survivors));
      }
    } catch {
      walkInFlight = false;
      totalCacheBytes = null;
      // Capacity management is best-effort: worst case the next write lands slightly over budget.
    }
  });
}

/**
 * Writes one entry and its metadata, retrying once against a ceiling learned from a refusal.
 *
 * `putEntryWithMetadata` resolving `false` is the browser saying no -- almost always
 * `QuotaExceededError`. Treating that as "skip the bookkeeping" (which is all it used to do)
 * leaves the running total frozen above the real limit, so every subsequent write short-circuits
 * on a budget check that can never be satisfied and fails in silence.
 */
async function persistEntry(
  cacheKey: string,
  entry: StoredLayerQueryEntry,
  metadata: StoredEntryMetadata,
  byteDelta: number,
  attribution: QueryKeyAttribution,
  isNewEntry: boolean
): Promise<void> {
  let written = await putEntryWithMetadata(STORE_CONFIG, cacheKey, entry, metadata);
  if (!written) {
    learnQuotaCeilingFromRefusal();
    await ensureCapacityFor(metadata.approxByteSize);
    written = await putEntryWithMetadata(STORE_CONFIG, cacheKey, entry, metadata);
  }
  if (!written) return;
  recordByteDelta(byteDelta);
  notifyEntryStored(cacheKey, attribution, metadata.expiresAt, byteDelta, isNewEntry);
}

/**
 * Every layer's currently-fresh synced days, and the expired rows swept on the way past.
 *
 * `null` means the store could not be read at all (no IndexedDB, blocked, private browsing) --
 * distinct from an empty index, which means "read it, nothing is synced". The caller must not
 * present those two the same way. Reads metadata only, so its cost is set by the NUMBER of
 * entries and not by their size, which is what lets it run on every app mount.
 */
export async function scanSyncedDays(): Promise<SyncedDayScan | null> {
  if (!isIndexedDbAvailable()) return null;
  return serializeFullPass(async () => {
    try {
      const walk = await walkCache();
      const committed = await deleteEntries(STORE_CONFIG, walk.deadKeys);
      commitWalkTotal(walk.liveBytes, committed);
      return { index: buildSyncedDayIndex(walk.live), coveredKeys: walk.coveredKeys };
    } catch {
      walkInFlight = false;
      totalCacheBytes = null;
      return null;
    }
  });
}

/**
 * Drops every persisted entry belonging to one layer -- the per-timeline reset -- and returns
 * the index the store is left in, or `null` when the store could not be read.
 */
export async function clearLayerEntries(layerId: LayerToggleId): Promise<SyncedDayIndex | null> {
  if (!isIndexedDbAvailable()) return null;
  return serializeFullPass(async () => {
    try {
      const walk = await walkCache();
      const doomedKeys = walk.live.filter((r) => r.layerId === layerId).map((r) => r.key);
      const committed = await deleteEntries(STORE_CONFIG, [...walk.deadKeys, ...doomedKeys]);
      if (!committed) {
        commitWalkTotal(walk.liveBytes, false);
        return null;
      }
      const survivors = walk.live.filter((r) => r.layerId !== layerId);
      commitWalkTotal(
        survivors.reduce((sum, record) => sum + record.approxByteSize, 0),
        true
      );
      return buildSyncedDayIndex(survivors);
    } catch {
      walkInFlight = false;
      totalCacheBytes = null;
      return null;
    }
  });
}

/** Concurrency-throttled background revalidation queue (max 2 active requests). */
const MAX_CONCURRENT_REVALIDATIONS = 2;
let activeRevalidations = 0;
const revalidationQueue: Array<() => void> = [];

export function getActiveRevalidationsCount(): number {
  return activeRevalidations;
}

export function getRevalidationQueueLength(): number {
  return revalidationQueue.length;
}

async function acquireRevalidationSlot(): Promise<void> {
  if (activeRevalidations < MAX_CONCURRENT_REVALIDATIONS) {
    activeRevalidations++;
    return;
  }
  await new Promise<void>((resolve) => {
    revalidationQueue.push(() => {
      activeRevalidations++;
      resolve();
    });
  });
}

function releaseRevalidationSlot(): void {
  activeRevalidations = Math.max(0, activeRevalidations - 1);
  const next = revalidationQueue.shift();
  if (next) {
    next();
  }
}

/** How this module reaches the QueryClient it is installed on; see `createIndexedDbLayerQueryPersister`. */
export type QueryClientAccessor = () => QueryClient | null;

/**
 * Publishes a background-revalidated value into react-query, which is the whole point of
 * revalidating: until 2026-08-16 the fresh payload was written to IndexedDB and dropped on the
 * floor, so every allowlisted layer rendered exactly one fetch behind, permanently, and only
 * caught up on the next mount of that same key.
 *
 * `queryClient.setQueryData` is the documented public way to do it. Two alternatives were
 * rejected: `query.setData()` -- the persister IS handed the `Query` -- is a `query-core` class
 * method that exists on the type but is not part of the documented app-facing API, so it is a
 * private-API trap that would break on a minor bump; and a module-global QueryClient set at
 * import time is hidden mutable state that silently binds to whichever client happened to
 * register last, which tests and any second client would get wrong. The accessor is passed in
 * from src/lib/providers.tsx, where the client is actually created.
 *
 * Nothing is published into a key react-query no longer holds: the IDB write already saved it
 * for the next mount, and materialising a cache entry for an unobserved query would only give
 * the garbage collector more to do.
 */
function publishToQueryClient(
  getQueryClient: QueryClientAccessor,
  queryKey: readonly unknown[],
  value: unknown
): void {
  try {
    const queryClient = getQueryClient();
    if (queryClient === null) return;
    if (queryClient.getQueryState(queryKey) === undefined) return;
    queryClient.setQueryData(queryKey, value);
  } catch {
    // Publishing is best-effort; the value is already durable in IndexedDB either way.
  }
}

/**
 * Whether a fresh hit is worth a background refetch: at most once per
 * REVALIDATION_MIN_INTERVAL_MS per entry, which bounds a layer to one background request a
 * minute however often its rows are re-read.
 *
 * Deliberately NOT gated on the day being historical. That gate existed for one afternoon and
 * was a correctness regression: with a 30-day TTL and no revalidation, a warehouse correction to
 * a past day could not reach the reader by ANY path for 30 days -- while the sync track drew
 * "saved on this device" over the stale answer. This warehouse rewrites historical days (see
 * HISTORICAL_TTL_MS above), so background revalidation IS the correction path, and the throttle
 * is the only thing that may bound it.
 */
function shouldRevalidate(stored: StoredLayerQueryEntry): boolean {
  const lastRevalidatedAt = stored.lastRevalidatedAt ?? stored.createdAt;
  return Date.now() - lastRevalidatedAt >= REVALIDATION_MIN_INTERVAL_MS;
}

/**
 * True while the entry we started from is still the one on disk.
 *
 * A revalidation can wait behind `MAX_CONCURRENT_REVALIDATIONS` for longer than `LIVE_TTL_MS`,
 * by which time the entry may have expired, missed, and been replaced by a NEWER fetch. Writing
 * and publishing the older result then reverts a layer the reader just watched update.
 * `createdAt` is the generation marker: revalidation preserves it, a cold write resets it.
 */
async function isStillTheSameGeneration(
  cacheKey: string,
  stored: StoredLayerQueryEntry
): Promise<boolean> {
  try {
    const onDisk = await getEntry<unknown>(STORE_CONFIG, cacheKey);
    if (onDisk === null || !isStoredLayerQueryEntry(onDisk)) return false;
    return onDisk.createdAt === stored.createdAt;
  } catch {
    return false;
  }
}

/**
 * Background SWR revalidation. Runs `queryFn` off the critical path, throttled to bound request
 * storms, and updates BOTH IndexedDB and the react-query cache -- the second half of that claim
 * was false until 2026-08-16. This is the only path by which a corrected warehouse day reaches a
 * reader who already has the old one cached.
 */
export async function revalidateAgainstDW<TContext>(
  queryFn: (context: TContext) => unknown,
  context: TContext,
  queryKey: readonly unknown[],
  cacheKey: string,
  stored: StoredLayerQueryEntry,
  getQueryClient: QueryClientAccessor = () => null
): Promise<unknown> {
  await acquireRevalidationSlot();
  try {
    const result = await queryFn(context);
    if (isCacheableResult(result)) {
      if (!(await isStillTheSameGeneration(cacheKey, stored))) return stored.value;
      const now = Date.now();
      const approxByteSize = estimateByteSize(result);
      const attribution = attributeQueryKey(queryKey);
      const expiresAt = now + resolveCacheTtlMs(queryKey);
      const byteDelta = approxByteSize - stored.approxByteSize;

      const updatedEntry: StoredLayerQueryEntry = {
        key: cacheKey,
        schemaVersion: CACHE_SCHEMA_VERSION,
        createdAt: stored.createdAt,
        expiresAt,
        approxByteSize,
        value: result,
        ...(attribution.layerId === null ? {} : { layerId: attribution.layerId }),
        ...(attribution.day === undefined ? {} : { day: attribution.day }),
        lastRevalidatedAt: now,
      };
      const metadata: StoredEntryMetadata = {
        key: cacheKey,
        schemaVersion: CACHE_SCHEMA_VERSION,
        expiresAt,
        lastAccessedAt: now,
        approxByteSize,
        ...(attribution.layerId === null ? {} : { layerId: attribution.layerId }),
        ...(attribution.day === undefined ? {} : { day: attribution.day }),
      };
      if (byteDelta > 0) await ensureCapacityFor(byteDelta);
      await persistEntry(cacheKey, updatedEntry, metadata, byteDelta, attribution, false);
      publishToQueryClient(getQueryClient, queryKey, result);
      return result;
    }
  } catch {
    // SWR background revalidation failure keeps existing stored value intact
  } finally {
    releaseRevalidationSlot();
  }
  return stored.value;
}

/**
 * Builds the persister wired into `defaultOptions.queries.persister`. For any query outside
 * the allowlist -- or when IndexedDB is unavailable at all -- it is a pure passthrough to
 * `queryFn`, with zero extra work.
 *
 * For an allowlisted query: a fresh cache hit returns the stored value immediately (0 ms
 * latency), bumps recency with a metadata-only write, and dispatches a throttled background
 * revalidation that publishes its result through `getQueryClient`.
 *
 * A factory rather than a ready-made constant because the persister signature is
 * `(queryFn, context, query)` and never receives a QueryClient; see `publishToQueryClient`
 * for why the client is closed over here instead of taken from the `Query` or a module global.
 */
export function createIndexedDbLayerQueryPersister(
  getQueryClient: QueryClientAccessor
): QueryPersister {
  return async (queryFn, context, query) => {
    const queryKey = query.queryKey as readonly unknown[];
    if (!isIndexedDbAvailable() || !isPersistableQueryKey(queryKey)) {
      return queryFn(context);
    }

    const cacheKey = query.queryHash;

    try {
      const stored = await getEntry<unknown>(STORE_CONFIG, cacheKey);
      if (stored !== null && isStoredLayerQueryEntry(stored)) {
        const attribution = attributeStoredRow(stored, cacheKey);
        if (isEntryFresh(stored)) {
          // Recency is a metadata-only write, so a cache hit rewrites ~100 bytes rather than
          // re-serializing the payload it just read.
          void putMetadata(STORE_CONFIG, cacheKey, {
            key: cacheKey,
            schemaVersion: stored.schemaVersion,
            expiresAt: stored.expiresAt,
            lastAccessedAt: Date.now(),
            approxByteSize: stored.approxByteSize,
            ...(attribution.layerId === null ? {} : { layerId: attribution.layerId }),
            ...(attribution.day === undefined ? {} : { day: attribution.day }),
          } satisfies StoredEntryMetadata).catch(() => {});
          if (shouldRevalidate(stored)) {
            void revalidateAgainstDW(
              queryFn,
              context,
              queryKey,
              cacheKey,
              stored,
              getQueryClient
            ).catch(() => {});
          }
          return stored.value;
        }
        void deleteEntry(STORE_CONFIG, cacheKey).catch(() => {});
        recordByteDelta(-stored.approxByteSize);
        notifyEntryDropped(cacheKey, attribution, stored.approxByteSize);
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
        const attribution = attributeQueryKey(queryKey);
        const expiresAt = now + resolveCacheTtlMs(queryKey);

        const entry: StoredLayerQueryEntry = {
          key: cacheKey,
          schemaVersion: CACHE_SCHEMA_VERSION,
          createdAt: now,
          expiresAt,
          approxByteSize,
          value: result,
          ...(attribution.layerId === null ? {} : { layerId: attribution.layerId }),
          ...(attribution.day === undefined ? {} : { day: attribution.day }),
          lastRevalidatedAt: now,
        };
        const metadata: StoredEntryMetadata = {
          key: cacheKey,
          schemaVersion: CACHE_SCHEMA_VERSION,
          expiresAt,
          lastAccessedAt: now,
          approxByteSize,
          ...(attribution.layerId === null ? {} : { layerId: attribution.layerId }),
          ...(attribution.day === undefined ? {} : { day: attribution.day }),
        };
        await ensureCapacityFor(approxByteSize);
        // Reaching here means the read above missed, so this key is new to the store: the day
        // gains an entry rather than replacing one.
        await persistEntry(cacheKey, entry, metadata, approxByteSize, attribution, true);
      }
    } catch {
      // Writing to the cache is best-effort; `result` below is still the real answer.
    }

    return result;
  };
}
