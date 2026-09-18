import { z } from "zod";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import { getParquetLatestRelease } from "@/lib/server/services/parquet-plane-client";
import {
  boundedResult,
  daySchema,
  decodePolygonGeometry,
  finiteNumberSchema,
  instantSchema,
  mapEnvelope,
  parseRows,
  rejectFutureDay,
  selectedDay,
  type ParquetPolygonGeometry,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

const evacuationZoneRowSchema = z
  .object({
    global_id: z.string().min(1),
    natural_key: z.string().min(1),
    producer: z.string().min(1),
    snapshot_day: daySchema,
    evacuation_area_name: z.string().nullable(),
    fire_name: z.string().nullable(),
    county: z.string().nullable(),
    hazard_type: z.string().nullable(),
    evacuation_level: z.number().int().nullable(),
    evacuation_level_label: z.string().nullable(),
    severity: z.string().nullable(),
    structures_within: finiteNumberSchema.nullable(),
    addresses_within: finiteNumberSchema.nullable(),
    population_within: finiteNumberSchema.nullable(),
    editor_name: z.string().nullable(),
    observed_at: instantSchema.nullable(),
    source: z.string().min(1),
    geometry_wkb: z.string().min(1),
    geometry_version_id: z.string().nullable(),
    geometry_version_valid_from: instantSchema.nullable(),
    geometry_last_confirmed_at: instantSchema.nullable(),
    data_available_at: instantSchema.nullable(),
    feature_updated_at: instantSchema.nullable(),
  })
  .strict();

export interface ParquetEvacuationZone {
  naturalKey: string;
  snapshotDay: string;
  evacuationAreaName: string | null;
  fireName: string | null;
  county: string | null;
  hazardType: string | null;
  evacuationLevel: number | null;
  evacuationLevelLabel: string | null;
  severity: string | null;
  structuresWithin: number | null;
  populationWithin: number | null;
  observedAt: string | null;
  geometry: ParquetPolygonGeometry;
}

/**
 * Every published evacuation area as of the requested day; one release IS the standing set.
 *
 * See `src/lib/server/services/AGENTS.md` §static-lookup-lanes.
 */
export async function getParquetEvacuationZones(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetEvacuationZone[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "evacuation-zones");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLatestRelease({
        layer: "evacuation-zones",
        asOfDay: day,
        zoomTier,
        ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      }),
      (rows) =>
        parseRows(rows, evacuationZoneRowSchema, "evacuation-zones").map((row) => ({
          naturalKey: row.natural_key,
          snapshotDay: row.snapshot_day,
          evacuationAreaName: row.evacuation_area_name,
          fireName: row.fire_name,
          county: row.county,
          hazardType: row.hazard_type,
          evacuationLevel: row.evacuation_level,
          evacuationLevelLabel: row.evacuation_level_label,
          severity: row.severity,
          structuresWithin: row.structures_within,
          populationWithin: row.population_within,
          observedAt: row.observed_at,
          geometry: decodePolygonGeometry(row.geometry_wkb, "evacuation-zones"),
        }))
    )
  );
}
