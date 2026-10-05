import { randomUUID } from "crypto";
import {
  UpstreamAbortedError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import { acquireCacheLock, cacheGeoJSON, getCachedGeoJSON, releaseCacheLock } from "@/lib/server/redis";
import { callRegionalEvidenceTool } from "@/lib/server/services/regional-evidence-tools";
import {
  DISTRIBUTION_AT_POINT_TOOL,
  distributionAtPointResultSchema,
  type DistributionAtPointResult,
} from "@/lib/layer-window-distribution";
import type { AnalysisLayerWindow } from "@/lib/regional-analysis-selection";

/** An answer whose every refusal is permanent; a window ending today can still fill in, so not longer. */
export const SETTLED_CACHE_SECONDS = 15 * 60;
/** An answer carrying a transient refusal (capacity, timeout, object store): ask again soon. */
export const TRANSIENT_CACHE_SECONDS = 30;
/** Agri timed out, was unreachable or answered 429/5xx: remembered so repeated hovers do not re-run the read. */
export const UNAVAILABLE_CACHE_SECONDS = 60;
/** Longer than the bridge's 15 s fetch bound, so a live leader never loses its lock mid-call. */
const SINGLE_FLIGHT_LOCK_SECONDS = 20;
const SINGLE_FLIGHT_POLL_MS = 250;
/** How long a caller that lost the lock waits for the leader's answer before a 503: a leader takes 2-11 s, under the 15 s bridge. */
const SINGLE_FLIGHT_WAIT_MS = 12_000;

/**
 * Refusal codes that the same question answers the same way next time: they depend on code,
 * configuration and the catalogue, never on load or the object store. Every other code (agri's
 * `serving_at_capacity`, `read_timed_out`, `read_over_budget`, `object_store_session_unavailable`,
 * `partition_day_incomplete`, ...) is transient. See services/AGENTS.md §window-distribution.
 */
export const PERMANENT_REFUSAL_CODES: ReadonlySet<string> = new Set([
  // Lane refusals (window_distribution.lane_distribution, selection_reads' static support check).
  "release_lane_not_distributed",
  "no_window_measure",
  "bbox_unsupported",
  // Whole-call refusals (window_distribution.distribution).
  "invalid_window",
  "unknown_surface",
  "app_surface_not_distributed",
  "not_available_in_region",
  "parquet_lane_not_published",
  "unknown_signal_name",
]);

/** The negative entry an unavailable agri leaves under the answer's own key. */
const UNAVAILABLE_ENTRY = { unavailable: true } as const;

/** Agri did not answer (cached negative entry, or a leader that never finished); the router maps it to 503. */
export class LayerWindowDistributionUnavailableError extends Error {}

/** Four decimal places is ~11 m: finer than any lane's cell, so neighbours share one answer. */
export function roundCoordinate(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export interface LayerWindowDistributionRequest extends AnalysisLayerWindow {
  surfaceName: string;
  longitude: number;
  latitude: number;
  /** One signal of a multi-signal surface (agri `signal_name`); undefined reads every lane. */
  signalName?: string;
}

/** 15 minutes only when every refusal in the answer (whole-call or per lane) is permanent; else 30 s. */
export function distributionCacheSeconds(result: DistributionAtPointResult): number {
  const refusalCodes = [
    ...(result.state === "refused" ? [result.refusal_code] : []),
    ...result.lanes.filter((lane) => lane.state === "refused").map((lane) => lane.refusal_code),
  ];
  return refusalCodes.every((code) => code !== null && PERMANENT_REFUSAL_CODES.has(code))
    ? SETTLED_CACHE_SECONDS
    : TRANSIENT_CACHE_SECONDS;
}

/** The faults that mean "agri is not answering right now", the ones worth a negative entry. */
function isAgriUnavailable(error: unknown): boolean {
  return (
    error instanceof UpstreamTimeoutError ||
    (error instanceof UpstreamHttpError && (error.status === 429 || error.status >= 500)) ||
    (error instanceof TypeError && error.message.includes("fetch failed"))
  );
}

type CachedDistribution = DistributionAtPointResult | typeof UNAVAILABLE_ENTRY | null;

async function readCached(cacheKey: string): Promise<CachedDistribution> {
  const raw = await getCachedGeoJSON<unknown>(cacheKey);
  if (raw !== null && typeof raw === "object" && (raw as { unavailable?: unknown }).unavailable === true) {
    return UNAVAILABLE_ENTRY;
  }
  const parsed = distributionAtPointResultSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

function answerOrThrow(cached: Exclude<CachedDistribution, null>): DistributionAtPointResult {
  if ("unavailable" in cached) {
    throw new LayerWindowDistributionUnavailableError("The environmental data service did not answer recently");
  }
  return cached;
}

/** Settles with `promise`, or rejects at once when the caller walks away; the promise itself runs on. */
function unlessAborted<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(new UpstreamAbortedError("The caller closed the request"));
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new UpstreamAbortedError("The caller closed the request"));
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", onAbort));
  });
}

function pause(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/** The caller that lost the lock: poll the cache for the leader's answer, then give up with a 503. */
async function awaitLeader(cacheKey: string, signal?: AbortSignal): Promise<DistributionAtPointResult> {
  const deadline = Date.now() + SINGLE_FLIGHT_WAIT_MS;
  while (Date.now() < deadline) {
    await unlessAborted(pause(SINGLE_FLIGHT_POLL_MS), signal);
    const cached = await readCached(cacheKey);
    if (cached !== null) return answerOrThrow(cached);
  }
  throw new LayerWindowDistributionUnavailableError("The distribution is still being computed");
}

/** The leader's one agri call; the answer (or a negative entry) is cached BEFORE the lock is released. */
async function fetchAndCache(cacheKey: string, args: Record<string, unknown>): Promise<DistributionAtPointResult> {
  let wire: string;
  try {
    // No caller signal: the agri read is spent whether or not this caller stays, so it is
    // finished and cached for whoever asks next (bounded by the bridge's own 15 s timeout).
    wire = await callRegionalEvidenceTool(DISTRIBUTION_AT_POINT_TOOL, args);
  } catch (error) {
    if (isAgriUnavailable(error)) await cacheGeoJSON(cacheKey, UNAVAILABLE_ENTRY, UNAVAILABLE_CACHE_SECONDS);
    throw error;
  }
  let body: unknown;
  try {
    body = JSON.parse(wire);
  } catch {
    throw new UpstreamPayloadError("Distribution response was not JSON");
  }
  const parsed = distributionAtPointResultSchema.safeParse(body);
  if (!parsed.success) throw new UpstreamPayloadError("Distribution response did not match its contract");
  await cacheGeoJSON(cacheKey, parsed.data, distributionCacheSeconds(parsed.data));
  return parsed.data;
}

/**
 * One layer's distribution over its window at a point, through the same agri bridge the analysis
 * workflow uses. Cached in Redis per (surface, signal, point rounded to 4 dp, window), with a
 * single-flight lock so identical concurrent misses share one agri call; see
 * services/AGENTS.md §window-distribution.
 */
export async function readLayerWindowDistribution(
  request: LayerWindowDistributionRequest,
  signal?: AbortSignal
): Promise<DistributionAtPointResult> {
  const longitude = roundCoordinate(request.longitude);
  const latitude = roundCoordinate(request.latitude);
  const cacheKey = [
    "layer-window-distribution:v2",
    request.surfaceName,
    request.signalName ?? "*",
    longitude.toFixed(4),
    latitude.toFixed(4),
    request.rangeStart,
    request.rangeEnd,
  ].join(":");
  const cached = await readCached(cacheKey);
  if (cached !== null) return answerOrThrow(cached);

  const lockKey = `${cacheKey}:lock`;
  const token = randomUUID();
  const lock = await acquireCacheLock(lockKey, token, SINGLE_FLIGHT_LOCK_SECONDS);
  if (lock === false) return awaitLeader(cacheKey, signal);

  const args: Record<string, unknown> = {
    surface_name: request.surfaceName,
    longitude,
    latitude,
    range_start: request.rangeStart,
    range_end: request.rangeEnd,
    ...(request.signalName === undefined ? {} : { signal_name: request.signalName }),
  };
  const flight = fetchAndCache(cacheKey, args).finally(() => {
    if (lock) void releaseCacheLock(lockKey, token);
  });
  return unlessAborted(flight, signal);
}
