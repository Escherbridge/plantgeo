import { ZOOM_TIERS } from "@/lib/map/zoom-tiers";
import type { ParquetLaneCoverage } from "@/lib/server/services/parquet-plane-client";
import { getParquetWarehouseCoverage } from "@/lib/server/services/parquet-plane-client";
import { isLandContextBoundInRegion } from "./region-binding";
import { LAND_CONTEXT_PRODUCT_LAYERS } from "./parquet-reader";
import type { LandContextGroupId } from "@/stores/land-context-store";

export interface LandContextGroupAvailability {
  group: LandContextGroupId;
  status: "available" | "not_published" | "unavailable";
  reason: string;
  latestDay: string | null;
}

/** Require a complete physical ladder for an edition; see AGENTS.md. */
export function publishedReleaseDaysAcrossRungs(lanes: readonly ParquetLaneCoverage[], layer: string): string[] {
  const ladder = ZOOM_TIERS.map((tier) => lanes.filter((lane) => lane.layer === layer &&
    lane.kind === "observed" && lane.zoomTier === tier && lane.withheldReason === null && lane.latestRecordedDay !== null));
  if (ladder.some((rung) => rung.length !== 1)) return [];
  const published = ladder.map((rung) => rung[0]);
  const candidates = new Set(published.flatMap((lane) => [
    lane.latestRecordedDay!,
    ...lane.publishedRanges.filter((range) => range.from === range.to).map((range) => range.from),
  ]));
  return [...candidates].filter((day) => published.every((lane) => lane.latestRecordedDay === day ||
    lane.publishedRanges.some((range) => range.from <= day && day <= range.to))).sort().reverse();
}

// Each family needs its own publication evidence; see AGENTS.md.
export async function readLandContextAvailability(): Promise<LandContextGroupAvailability[]> {
  const missing: LandContextGroupAvailability[] = [
    { group: "parcels-land-use", status: "unavailable", reason: "Parcel boundaries and recorded land use are not available. Crop estimates do not identify ownership.", latestDay: null },
    { group: "electric-utility-territories", status: "unavailable", reason: "Electric utility service territories are not available.", latestDay: null },
    { group: "state-managed-lands", status: "unavailable", reason: "State-managed land boundaries are not available.", latestDay: null },
  ];
  if (!isLandContextBoundInRegion()) {
    return [...missing, { group: "blm-lands", status: "unavailable", reason: "BLM boundaries are not available in this region.", latestDay: null }];
  }
  const census = await getParquetWarehouseCoverage();
  const latestDay = publishedReleaseDaysAcrossRungs(census.lanes, LAND_CONTEXT_PRODUCT_LAYERS.boundaries)[0] ?? null;
  return [...missing, {
    group: "blm-lands",
    status: latestDay === null ? "not_published" : "available",
    reason: latestDay === null ? "BLM boundaries have not been published for this region yet." : `BLM surface-management reference published ${latestDay}.`,
    latestDay,
  }];
}
