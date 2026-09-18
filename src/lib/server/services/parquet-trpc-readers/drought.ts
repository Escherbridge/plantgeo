import { z } from "zod";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import { getParquetLatestRelease } from "@/lib/server/services/parquet-plane-client";
import type { ParquetPlaneEnvelope } from "@/lib/server/services/parquet-envelope";
import {
  boundedResult,
  contractError,
  currentUtcDay,
  DAY_MS,
  daySchema,
  decodePolygonGeometry,
  envelopeDayMs,
  instantSchema,
  mapEnvelope,
  parseRows,
  rejectFutureDay,
  selectedDay,
  type ParquetPolygonGeometry,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

const DROUGHT_RELEASE_INTERVAL_DAYS = 7;
const DROUGHT_MAX_CARRY_FORWARD_DAYS = 14;

const droughtRowSchema = z
  .object({
    area_id: z.string().min(1),
    valid_date: daySchema,
    dm_category: z.number().int().min(0).max(4),
    source_url: z.string().min(1),
    ingested_at: instantSchema,
    geom: z.string().min(1),
  })
  .strict();

export interface ParquetDroughtArea {
  areaId: string;
  validDate: string;
  droughtCategory: 0 | 1 | 2 | 3 | 4;
  sourceUrl: string;
  ingestedAt: string;
  geometry: ParquetPolygonGeometry;
}

/** The release day an envelope answered with, refusing any state that cannot name one. */
function releaseServedDay(envelope: ParquetPlaneEnvelope, asOfDay: string): string {
  if (envelope.state !== "published" && envelope.state !== "governed_absence") {
    throw contractError(
      `drought release lookup for ${asOfDay} contradicted an earlier published release with ${envelope.state}`
    );
  }
  return envelope.servedDay;
}

/**
 * The USDM release standing at the requested day, carried forward only as far as the weekly
 * cadence allows. See `src/lib/server/services/AGENTS.md` §drought-carry-forward.
 */
export async function getParquetDrought(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetDroughtArea[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "drought");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const releaseRequest = {
      layer: "drought",
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    } as const;
    const envelope = await getParquetLatestRelease({
      ...releaseRequest,
      asOfDay: day,
    });
    const result = mapEnvelope(
      envelope,
      (rows) =>
        parseRows(rows, droughtRowSchema, "drought").map((row) => ({
          areaId: row.area_id,
          validDate: row.valid_date,
          droughtCategory: row.dm_category as 0 | 1 | 2 | 3 | 4,
          sourceUrl: row.source_url,
          ingestedAt: row.ingested_at,
          geometry: decodePolygonGeometry(row.geom, "drought"),
        }))
    );
    if (result.state !== "ready" && result.state !== "absent") return result;

    const requestedMs = envelopeDayMs(day, "requested");
    const servedMs = envelopeDayMs(result.servedDay, "served");
    const carryForwardDays = Math.round((requestedMs - servedMs) / DAY_MS);
    if (carryForwardDays < 0) {
      throw contractError(`drought release ${result.servedDay} is after requested day ${day}`);
    }
    if (result.state === "ready" && result.data.some((row) => row.validDate !== result.servedDay)) {
      throw contractError("drought rows do not match the release day reported by the Parquet plane");
    }

    const today = currentUtcDay(nowMs);
    let maxCarryForwardDays = DROUGHT_MAX_CARRY_FORWARD_DAYS;
    if (day < today) {
      const latestEnvelope = await getParquetLatestRelease({
        ...releaseRequest,
        asOfDay: today,
      });
      const latestServedDay = releaseServedDay(latestEnvelope, today);
      const latestServedMs = envelopeDayMs(latestServedDay, "latest served");
      if (latestServedMs > envelopeDayMs(today, "latest requested")) {
        throw contractError(`drought release ${latestServedDay} is after server UTC today ${today}`);
      }
      if (latestServedMs > servedMs && latestServedMs <= requestedMs) {
        throw contractError(
          `drought lookup for ${day} skipped the newer stored release ${latestServedDay}`
        );
      }
      if (latestServedMs > servedMs) {
        maxCarryForwardDays = DROUGHT_RELEASE_INTERVAL_DAYS - 1;
      }
    }
    if (carryForwardDays > maxCarryForwardDays) {
      return { state: "not_generated", requestedDay: day, reason: "day_not_written" };
    }
    return result;
  });
}
