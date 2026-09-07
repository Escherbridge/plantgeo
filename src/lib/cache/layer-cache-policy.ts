/**
 * Per-layer local cache policy: what a layer's NATURE implies by default, and how a user's
 * override composes over it.
 *
 * Deliberately PURE -- no zustand, no IndexedDB, no react-query, no React. Both
 * `layer-cache-policy-store.ts` (which holds the user's overrides) and `query-persister.ts`
 * (which enforces the resolved policy) import this, and neither may import the other's owner.
 * See src/lib/cache/AGENTS.md "per-layer cache policy" for the full rationale.
 */

import {
  CLIMATE_FIELD_TOGGLE_IDS,
  type ClimateFieldToggleId,
} from "@/lib/environmental/climate-field";
import { LAYER_TOGGLE_IDS, isLayerToggleId, type LayerToggleId } from "@/lib/map/layer-registry";

/**
 * How a layer's warehouse lane moves over time. The same three-member vocabulary the server's
 * `ParquetLaneNature` uses (`src/lib/server/services/parquet-plane-client.ts`), restated here
 * rather than imported because that module reaches DuckDB and this one is imported by the
 * browser bundle. `layer-cache-policy.test.ts` cross-checks the two tables agree.
 */
export type LayerCacheNature = "static_lookup" | "daily_series" | "release_series";

/** Whether the cache may refresh a layer on its own, or only when the user asks. */
export type LayerRefreshMode = "manual" | "automatic";

/**
 * How long ONE entry of a manual layer is served from disk.
 *
 * A year, and the length is the point: "manual" has to mean the cache never goes to the network
 * behind the user's back, and a TTL is a scheduled refetch wearing a different name. Anything
 * short enough to be a "safety net" is also short enough to be exactly the chatter the setting
 * exists to stop. What replaces the safety net is that the staleness is VISIBLE -- the control
 * renders `lastFetchedAt` beside the refetch button -- rather than silently corrected.
 */
export const MANUAL_TTL_MS = 365 * 24 * 60 * 60 * 1000;

/** Below this a retention limit is not a limit, it is a disabled cache; use the reset instead. */
export const MIN_RETAINED_DAY_LIMIT = 1;

/** Above this the limit is indistinguishable from `null`; fire-detections has 8,371 days total. */
export const MAX_RETAINED_DAY_LIMIT = 10_000;

/**
 * The refresh mode each nature gets when the user has said nothing.
 *
 * A blanket policy would be wrong in both directions, which is the whole reason this table
 * exists. `static_lookup` lanes publish a VERSION STAMP rather than a day -- `watersheds` has
 * published exactly one version in its entire history -- so background revalidation of them is
 * a request per minute per entry for a byte-identical answer. `release_series` lanes move in
 * irregular jumps (burn-severity: five releases across 2015-2026), so polling them is the same
 * waste at a slower rate. `daily_series` lanes genuinely grow every day AND get republished
 * behind the reader (the agri gap-reopen lane; see `HISTORICAL_TTL_MS` in query-persister.ts),
 * so for those the background revalidation is a CORRECTNESS path and stays on by default.
 */
export const DEFAULT_REFRESH_MODE: Readonly<Record<LayerCacheNature, LayerRefreshMode>> = {
  static_lookup: "manual",
  release_series: "manual",
  daily_series: "automatic",
};

/**
 * How many distinct DAYS of each nature are kept on disk before the least-recently-used ones go.
 *
 * `null` is "unlimited", bounded only by the global byte budget. `static_lookup` gets it because
 * those lanes barely have days at all: `getWatersheds` carries no date, so its entries are never
 * indexed against a day and a day-count limit would govern nothing. The other two get finite
 * defaults because their entries accumulate one per (day, viewport) forever otherwise -- and
 * fire-detections, the largest lane at 8,371 days back to 2000-11-01, is exactly the layer a
 * reader can fill a 512 MB budget with by scrubbing.
 */
export const DEFAULT_RETAINED_DAY_LIMIT: Readonly<Record<LayerCacheNature, number | null>> = {
  static_lookup: null,
  release_series: 180,
  daily_series: 90,
};

/**
 * The nature every registry layer is treated as.
 *
 * The eleven lanes that appear in the server's `DIRECT_PARQUET_CAPABILITIES` table carry that
 * table's `parquetNature` verbatim, keyed here by the toggle rather than by the warehouse stream
 * name (`layer-registry.ts` owns that mapping and nothing else may mint a second one). The rest
 * are DECLARED here because they have no Parquet lane to inherit from, and each is called out
 * below -- a nature that looks contract-derived but is not is the failure mode this comment
 * exists to prevent.
 */
const NATURE_BY_LAYER: Readonly<Record<LayerToggleId, LayerCacheNature>> = {
  // Nine climate rows, spread rather than listed for the same reason the registry spreads them:
  // nine hand-typed keys is nine chances to name a toggle that does not exist. Every one of them
  // is `daily_series` in the server's `SIGNAL_PARQUET_CAPABILITIES`. It goes FIRST so the rest of
  // the literal is still checked for exhaustiveness against `LayerToggleId` -- a registry layer
  // added with no nature here fails to compile rather than silently taking the fallback.
  ...(Object.fromEntries(
    CLIMATE_FIELD_TOGGLE_IDS.map((toggleId) => [toggleId, "daily_series"])
  ) as Record<ClimateFieldToggleId, LayerCacheNature>),

  // --- from the server's parquet lane contract ---
  fire: "daily_series",
  "fire-perimeters": "static_lookup",
  water: "daily_series",
  drought: "release_series",
  weather: "daily_series",
  sensors: "daily_series",
  watersheds: "static_lookup",
  vegetation: "daily_series",
  "soil-survey": "static_lookup",
  "evacuation-zones": "static_lookup",
  "burn-severity": "release_series",
  "soil-moisture": "daily_series",
  "soil-temperature": "daily_series",
  "soil-vpd": "daily_series",

  // --- declared here; no Parquet lane contract answers for these ---
  // SoilGrids rasters: a fixed global property surface, and the row is permanently withheld
  // anyway, so nothing routes a cacheable query to it.
  soil: "static_lookup",
  // Client-derived from whatever is already on the map; it has no warehouse lane and no
  // allowlisted procedure, so this entry only exists to keep the record exhaustive.
  "demand-heatmap": "static_lookup",
  // User-authored rows in `geo.features`; they change whenever somebody adds one.
  interventions: "daily_series",
  // The governed model plane republishes in whole releases, not per day.
  "strategy-recommendations": "release_series",
};

/**
 * What an unattributable query is treated as: exactly the behaviour that shipped before this
 * module existed (automatic revalidation, 30-day historical / 5-minute live TTL). A router path
 * this cache cannot map to a layer must not silently acquire a year-long TTL.
 */
export const FALLBACK_NATURE: LayerCacheNature = "daily_series";

/** The nature a layer is treated as. Total over the registry; never throws. */
export function layerCacheNature(layerId: LayerToggleId | null): LayerCacheNature {
  if (layerId === null) return FALLBACK_NATURE;
  return NATURE_BY_LAYER[layerId] ?? FALLBACK_NATURE;
}

/** Exported for the exhaustiveness test; production code goes through `layerCacheNature`. */
export function layerCacheNatureTable(): Readonly<Record<LayerToggleId, LayerCacheNature>> {
  return NATURE_BY_LAYER;
}

/**
 * A user's departure from the nature default. A field left `undefined` is NOT overridden and
 * follows the default; `retainedDayLimit: null` is a deliberate "keep everything" and is a
 * different fact from the field being absent.
 */
export interface LayerCacheOverride {
  refreshMode?: LayerRefreshMode;
  retainedDayLimit?: number | null;
}

/** The policy actually in force for one layer, defaults and override already composed. */
export interface LayerCachePolicy {
  nature: LayerCacheNature;
  refreshMode: LayerRefreshMode;
  /** Distinct days kept on disk; `null` means unlimited (the byte budget still applies). */
  retainedDayLimit: number | null;
  /** True when at least one field departs from the nature default. */
  isOverridden: boolean;
}

/** The policy a layer would have with nothing set by the user. */
export function defaultLayerCachePolicy(nature: LayerCacheNature): LayerCachePolicy {
  return {
    nature,
    refreshMode: DEFAULT_REFRESH_MODE[nature],
    retainedDayLimit: DEFAULT_RETAINED_DAY_LIMIT[nature],
    isOverridden: false,
  };
}

/**
 * Layers whose nature is right but whose NATURE DEFAULT is wrong for them, each with its reason.
 *
 * `DEFAULT_REFRESH_MODE` argues `static_lookup: "manual"` from watersheds -- "published exactly one
 * version in its entire history". That is true of watersheds and of soil-survey. It is FALSE of
 * evacuation zones, whose whole lane exists because Oregon OEM's set changes: its watermark is a
 * CONTENT DIGEST recomputed on an hourly poll (`pipeline/parquet/lane_registry.py`, the
 * evacuation-zones registration), and its published population moved 677 -> 718 -> 116 rows inside
 * three weeks.
 *
 * Left on the nature default, a reader who opened the map before a fire would be served that
 * snapshot from disk for up to `MANUAL_TTL_MS` (365 days) unless they thought to press refetch, on
 * the one layer where a stale answer is a life-safety answer. The nature stays `static_lookup` --
 * it genuinely publishes version stamps, and its retention default is still right -- so this is an
 * exception to one field, declared beside the table it departs from rather than hidden as a
 * special case in the resolver.
 *
 * A user may still override this in either direction; an exception is a DEFAULT, not a lock.
 */
const REFRESH_MODE_EXCEPTIONS: Partial<Record<LayerToggleId, LayerRefreshMode>> = {
  "evacuation-zones": "automatic",
};

/** Exported for the exception test; production code goes through `resolveLayerCachePolicy`. */
export function refreshModeExceptionTable(): Partial<Record<LayerToggleId, LayerRefreshMode>> {
  return REFRESH_MODE_EXCEPTIONS;
}

/** The nature default with any declared per-layer exception applied, before the user's override. */
function baseLayerCachePolicy(layerId: LayerToggleId | null): LayerCachePolicy {
  const nature = layerCacheNature(layerId);
  const base = defaultLayerCachePolicy(nature);
  const exception = layerId === null ? undefined : REFRESH_MODE_EXCEPTIONS[layerId];
  return exception === undefined ? base : { ...base, refreshMode: exception };
}

/** Nature default with the user's override composed over it. Total; never throws. */
export function resolveLayerCachePolicy(
  layerId: LayerToggleId | null,
  override: LayerCacheOverride | undefined
): LayerCachePolicy {
  const nature = layerCacheNature(layerId);
  const base = baseLayerCachePolicy(layerId);
  if (override === undefined) return base;
  const refreshModeOverridden = override.refreshMode !== undefined;
  const limitOverridden = override.retainedDayLimit !== undefined;
  if (!refreshModeOverridden && !limitOverridden) return base;
  return {
    nature,
    refreshMode: override.refreshMode ?? base.refreshMode,
    retainedDayLimit: limitOverridden
      ? clampRetainedDayLimit(override.retainedDayLimit ?? null)
      : base.retainedDayLimit,
    isOverridden: true,
  };
}

/** `null` passes through as "unlimited"; anything else is coerced into the usable band. */
export function clampRetainedDayLimit(limit: number | null): number | null {
  if (limit === null) return null;
  if (!Number.isFinite(limit)) return null;
  return Math.min(MAX_RETAINED_DAY_LIMIT, Math.max(MIN_RETAINED_DAY_LIMIT, Math.floor(limit)));
}

function isLayerRefreshMode(value: unknown): value is LayerRefreshMode {
  return value === "manual" || value === "automatic";
}

/**
 * Drops keys that are not registry layers and coerces every value.
 *
 * Follows `sanitizeLayerOpacity` in src/stores/layer-store.ts for the same reason it exists
 * there: a hand-edited localStorage blob reaches this store on the next reload, and a
 * `retainedDayLimit: 0` written that way would evict a layer's whole cache on every write.
 */
export function sanitizeLayerCacheOverrides(
  value: unknown
): Partial<Record<LayerToggleId, LayerCacheOverride>> {
  const sanitized: Partial<Record<LayerToggleId, LayerCacheOverride>> = {};
  if (typeof value !== "object" || value === null) return sanitized;
  for (const [layerId, raw] of Object.entries(value as Record<string, unknown>)) {
    if (!isLayerToggleId(layerId)) continue;
    if (typeof raw !== "object" || raw === null) continue;
    const candidate = raw as { refreshMode?: unknown; retainedDayLimit?: unknown };
    const override: LayerCacheOverride = {};
    if (isLayerRefreshMode(candidate.refreshMode)) override.refreshMode = candidate.refreshMode;
    if (candidate.retainedDayLimit === null) {
      override.retainedDayLimit = null;
    } else if (typeof candidate.retainedDayLimit === "number") {
      override.retainedDayLimit = clampRetainedDayLimit(candidate.retainedDayLimit);
    }
    // An override with nothing left in it is the same fact as no override at all.
    if (override.refreshMode !== undefined || override.retainedDayLimit !== undefined) {
      sanitized[layerId] = override;
    }
  }
  return sanitized;
}

/** Same discipline for the two epoch-millisecond maps this store persists beside the overrides. */
export function sanitizeLayerTimestamps(value: unknown): Partial<Record<LayerToggleId, number>> {
  const sanitized: Partial<Record<LayerToggleId, number>> = {};
  if (typeof value !== "object" || value === null) return sanitized;
  for (const [layerId, at] of Object.entries(value as Record<string, unknown>)) {
    if (!isLayerToggleId(layerId)) continue;
    if (typeof at !== "number" || !Number.isFinite(at) || at <= 0) continue;
    sanitized[layerId] = at;
  }
  return sanitized;
}

/** Every registry layer, for a control that enumerates them. Re-exported so the UI needs one import. */
export function cacheGovernedLayerIds(): readonly LayerToggleId[] {
  return LAYER_TOGGLE_IDS;
}
