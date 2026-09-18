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

/**
 * The WFIGS incident row as the warehouse registers it; `.strict()` so an upstream column change
 * fails loudly. See `src/lib/server/services/AGENTS.md` §polygon-lanes.
 */
const firePerimeterRowSchema = z
  .object({
    feature_id: z.string().min(1),
    unique_fire_identifier: z.string().min(1),
    // The VERSION STAMP this whole partition shares, not an observation day.
    snapshot_day: daySchema,
    // NULLABLE BY CONTRACT: a row the upstream cannot date is drawn at EVERY slider date.
    // See `firePerimetersInFrame`.
    observed_day: daySchema.nullable(),
    incident_name: z.string().nullable(),
    irwin_id: z.string().nullable(),
    fire_discovery_at: instantSchema.nullable(),
    polygon_at: instantSchema.nullable(),
    gis_acres: finiteNumberSchema.nullable(),
    fire_cause: z.string().nullable(),
    incident_type_category: z.string().nullable(),
    poo_state: z.string().nullable(),
    percent_contained: finiteNumberSchema.nullable(),
    severity: z.string().nullable(),
    status: z.string().min(1),
    data_available_at: instantSchema.nullable(),
    updated_at: instantSchema,
    geometry_wkb: z.string().min(1),
  })
  .strict();

/**
 * One WFIGS incident, as of whichever snapshot answered the request.
 *
 * Six projected fields out of the eighteen validated above; widening the tooltip is a
 * `hover-fields.ts` change. See `src/lib/server/services/AGENTS.md` §fire-perimeters.
 */
export interface ParquetFirePerimeter {
  featureId: string;
  uniqueFireIdentifier: string;
  /** The day this population was CAPTURED -- never the day an incident was observed. */
  snapshotDay: string;
  /** The incident's own date, or null for a row WFIGS gave no parseable timestamp. */
  observedDay: string | null;
  severity: string | null;
  geometry: ParquetPolygonGeometry;
}

/**
 * The rows of one resolved snapshot that are IN FRAME at the day a caller asked for.
 *
 * Two load-bearing properties, both transcribed from the plane's own
 * `observed_day IS NULL OR observed_day <= as_of`: an UNDATED incident is never excluded, and the
 * comparison is against `asOfDay` (the REQUESTED day), never the served snapshot's capture day.
 * A pure day-string comparison, never `Date.parse` -- the named-day rule forbids turning a
 * published day into an instant here. See `src/lib/server/services/AGENTS.md` §fire-perimeters.
 */
function firePerimetersInFrame<T extends { observed_day: string | null }>(
  rows: readonly T[],
  asOfDay: string
): T[] {
  return rows.filter((row) => row.observed_day === null || row.observed_day <= asOfDay);
}

/**
 * Every WFIGS incident current as of the requested day, from the newest snapshot at or before it.
 *
 * The snapshot-resolution rule belongs to `getParquetLatestRelease`; what this reader owes on top
 * of it is the in-frame filter. `servedDay` is the SNAPSHOT day and `requestedDay` the slider day.
 * See `src/lib/server/services/AGENTS.md` §fire-perimeters.
 */
export async function getParquetFirePerimeters(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetFirePerimeter[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "fire-perimeters");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLatestRelease({
        layer: "fire-perimeters",
        asOfDay: day,
        zoomTier,
        ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      }),
      (rows) =>
        // `day`, the REQUESTED day, is the only date this filter may read. The served snapshot day
        // is deliberately out of scope here -- it is not in this closure's argument list at all.
        firePerimetersInFrame(
          parseRows(rows, firePerimeterRowSchema, "fire-perimeters"),
          day
        ).map((row) => ({
          featureId: row.feature_id,
          uniqueFireIdentifier: row.unique_fire_identifier,
          snapshotDay: row.snapshot_day,
          observedDay: row.observed_day,
          severity: row.severity,
          geometry: decodePolygonGeometry(row.geometry_wkb, "fire-perimeters"),
        }))
    )
  );
}
