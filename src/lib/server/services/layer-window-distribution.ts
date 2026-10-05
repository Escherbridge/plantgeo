import { UpstreamPayloadError } from "@/lib/server/http/bounded-upstream";
import { cacheGeoJSON, getCachedGeoJSON } from "@/lib/server/redis";
import { callRegionalEvidenceTool } from "@/lib/server/services/regional-evidence-tools";
import {
  DISTRIBUTION_AT_POINT_TOOL,
  distributionAtPointResultSchema,
  type DistributionAtPointResult,
} from "@/lib/layer-window-distribution";
import type { AnalysisLayerWindow } from "@/lib/regional-analysis-selection";

/** Cache lifetime for one (surface, point, window) answer; a window ending today can still fill in. */
const DISTRIBUTION_CACHE_SECONDS = 15 * 60;

/** Four decimal places is ~11 m: finer than any lane's cell, so neighbours share one answer. */
export function roundCoordinate(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export interface LayerWindowDistributionRequest extends AnalysisLayerWindow {
  surfaceName: string;
  longitude: number;
  latitude: number;
}

/**
 * One layer's distribution over its window at a point, through the same agri bridge the analysis
 * workflow uses. Cached in Redis per (surface, point rounded to 4 dp, window); see
 * services/AGENTS.md §window-distribution.
 */
export async function readLayerWindowDistribution(
  request: LayerWindowDistributionRequest,
  signal?: AbortSignal
): Promise<DistributionAtPointResult> {
  const longitude = roundCoordinate(request.longitude);
  const latitude = roundCoordinate(request.latitude);
  const cacheKey = [
    "layer-window-distribution:v1",
    request.surfaceName,
    longitude.toFixed(4),
    latitude.toFixed(4),
    request.rangeStart,
    request.rangeEnd,
  ].join(":");
  const cached = distributionAtPointResultSchema.safeParse(await getCachedGeoJSON<unknown>(cacheKey));
  if (cached.success) return cached.data;

  const wire = await callRegionalEvidenceTool(
    DISTRIBUTION_AT_POINT_TOOL,
    {
      surface_name: request.surfaceName,
      longitude,
      latitude,
      range_start: request.rangeStart,
      range_end: request.rangeEnd,
    },
    signal
  );
  let body: unknown;
  try {
    body = JSON.parse(wire);
  } catch {
    throw new UpstreamPayloadError("Distribution response was not JSON");
  }
  const parsed = distributionAtPointResultSchema.safeParse(body);
  if (!parsed.success) throw new UpstreamPayloadError("Distribution response did not match its contract");
  await cacheGeoJSON(cacheKey, parsed.data, DISTRIBUTION_CACHE_SECONDS);
  return parsed.data;
}
