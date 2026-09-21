import { z } from "zod";
import { getRegion } from "@/lib/region/region";
import { getParquetLatestRelease, getParquetWarehouseCoverage } from "@/lib/server/services/parquet-plane-client";
import { decodePublishedGeometry } from "./geometry/boundary-geometry-adapter";
import { bboxSquareDegrees, type BboxDegrees } from "./parquet-reader";
import { ZOOM_TIERS, type ZoomTier } from "@/lib/map/zoom-tiers";
import { selectFinestAdmittingRungResult } from "@/lib/map/rung-selection";
import { publishedReleaseDaysAcrossRungs } from "./availability";
import { MAX_AOI_AREA_SQUARE_DEGREES, MAX_RESPONSE_BYTES } from "./budgets";
import { daySchema, envelopeDayMs } from "@/lib/server/services/parquet-trpc-readers/shared";

const CROP_LAYER = "crop-cover";
const CROP_RUNG_MAX_AREA: Readonly<Record<ZoomTier, number>> = { 13: 0.5, 9: 8, 5: 128, 0: 64_800 };
const cropRow = z.object({
  feature_id: z.string().min(1),
  observed_year: z.number().int(),
  release_day: daySchema,
  source: z.literal("usda-cdl"),
  source_url: z.string().url(),
  source_resolution_m: z.number().positive(),
  analysis_resolution_m: z.number().positive(),
  aggregation_cell_m: z.number().positive(),
  estimation_method: z.enum(["classified_pixel_area", "nearest_neighbor_resampled_pixel_area"]),
  dominant_crop_code: z.number().int().nullable(),
  dominant_crop_name: z.string().nullable(),
  crop_fraction: z.number().min(0).max(1),
  classified_fraction: z.number().min(0).max(1),
  crop_area_ha: z.number().nonnegative(),
  cell_area_ha: z.number().positive(),
  class_areas_json: z.string(),
  class_names_json: z.string().optional(),
  grid_x: z.number().int().optional(),
  grid_y: z.number().int().optional(),
  geometry_wkb: z.unknown(),
  source_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  ingested_at: z.string(),
});

function isCropBound(): boolean {
  return getRegion().enabledLayers.some((binding) => binding.layerSlug === CROP_LAYER);
}

export async function readCropCoverAvailability() {
  if (!isCropBound()) return { available: false, latestDay: null, releaseDays: [] as string[], reason: "Estimated crop cover is not available in this region." };
  const census = await getParquetWarehouseCoverage();
  const releaseDays = publishedReleaseDaysAcrossRungs(census.lanes, CROP_LAYER);
  const latestDay = releaseDays[0] ?? null;
  return { available: latestDay !== null, latestDay, releaseDays, reason: latestDay ? "USDA Cropland Data Layer estimates." : "Crop-cover estimates have not been published yet." };
}

/** Annual classified-image estimates, bounded by view and publication day; see AGENTS.md. */
export async function readCropCover(bbox: BboxDegrees, asOfDay: string, zoomTier: ZoomTier) {
  daySchema.parse(asOfDay);
  envelopeDayMs(asOfDay, "requested publication day");
  const empty = (message: string) => ({ geojson: null, servedDay: null, truncated: false, message });
  if (!isCropBound()) return empty("Estimated crop cover is not available in this region.");
  const area = bboxSquareDegrees(bbox);
  if (area > MAX_AOI_AREA_SQUARE_DEGREES) return empty("Zoom in to read estimated crop cover.");
  const census = await getParquetWarehouseCoverage();
  const readable = census.lanes.filter((lane) => lane.layer === CROP_LAYER && lane.kind === "observed" &&
    lane.withheldReason === null && lane.latestDay !== null);
  const selection = selectFinestAdmittingRungResult({
    coarsestFirst: ZOOM_TIERS,
    maxBboxSquareDegrees: CROP_RUNG_MAX_AREA,
    areaSquareDegrees: area,
    finestAllowed: zoomTier,
    isPublished: (tier) => readable.some((lane) => lane.zoomTier === tier),
  });
  if (selection.kind !== "selected") return empty("No published crop-cover resolution serves this view.");
  const envelope = await getParquetLatestRelease({
    layer: CROP_LAYER, asOfDay, zoomTier: selection.rung, kind: "observed",
    bbox: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`,
  });
  if (envelope.state !== "published") {
    return empty(envelope.state === "governed_absence"
      ? `No crop-cover estimate is available: ${envelope.evidence.upstreamResponse}`
      : `No crop-cover release is available by ${asOfDay}.`);
  }
  const rows = z.array(cropRow).parse(envelope.rows);
  if (rows.length > 2_000) return empty("This view contains too many crop-cover cells. Zoom in to read a smaller area.");
  const features = rows.map((row): GeoJSON.Feature => {
    const { geometry_wkb, ...properties } = row;
    const geometry = decodePublishedGeometry(geometry_wkb);
    if (geometry === null) throw new Error("Published crop-cover cell has no geometry");
    if (row.release_day > asOfDay) throw new Error("Crop-cover release is newer than the requested day");
    if (row.release_day !== envelope.servedDay) throw new Error("Crop-cover row disagrees with its published release");
    return { type: "Feature", id: row.feature_id, geometry, properties };
  });
  if (Buffer.byteLength(JSON.stringify(features), "utf8") > MAX_RESPONSE_BYTES) {
    return empty("This view contains too much crop-cover detail. Zoom in to read a smaller area.");
  }
  return {
    geojson: { type: "FeatureCollection" as const, features },
    servedDay: envelope.servedDay,
    truncated: envelope.truncated,
    message: envelope.truncated ? "Only part of this view was returned. Zoom in to inspect more crop-cover cells." :
      features.length ? "Estimated crop cover from classified satellite imagery. Cells are not parcels; area shares are not confidence scores." :
        "The published release contains no crop-cover cells for this view.",
  };
}
