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

const watershedRowSchema = z
  .object({
    huc12: z.string().min(1),
    name: z.string().nullable(),
    areasqkm: finiteNumberSchema.nullable(),
    tohuc: z.string().nullable(),
    states: z.string().nullable(),
    hutype: z.string().nullable(),
    source: z.string().min(1),
    observed_at: instantSchema.nullable(),
    data_available_at: instantSchema.nullable(),
    release_day: daySchema,
    feature_id: z.string().nullable(),
    geom: z.string().min(1),
  })
  .strict();

/**
 * One published basin, at whatever rung of the HUC hierarchy served the request.
 *
 * `hucLevel` is the LENGTH of the code and never an assumption; see
 * `src/lib/server/services/AGENTS.md` §watersheds.
 */
export interface ParquetWatershed {
  huc: string;
  hucLevel: number;
  name: string | null;
  areaSquareKm: number | null;
  toHuc: string | null;
  states: string | null;
  huType: string | null;
  releaseDay: string;
  observedAt: string | null;
  geometry: ParquetPolygonGeometry;
}

/**
 * The USGS WBD basin set at the rung the camera can draw, as of the requested day.
 *
 * See `src/lib/server/services/AGENTS.md` §watersheds for the rung mapping, which is NOT the
 * retired tile function's.
 */
export async function getParquetWatersheds(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetWatershed[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "watersheds");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLatestRelease({
        layer: "watersheds",
        asOfDay: day,
        zoomTier,
        ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      }),
      (rows) =>
        parseRows(rows, watershedRowSchema, "watersheds").map((row) => ({
          huc: row.huc12,
          hucLevel: row.huc12.length,
          name: row.name,
          areaSquareKm: row.areasqkm,
          toHuc: row.tohuc,
          states: row.states,
          huType: row.hutype,
          releaseDay: row.release_day,
          observedAt: row.observed_at,
          geometry: decodePolygonGeometry(row.geom, "watersheds"),
        }))
    )
  );
}
