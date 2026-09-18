import { z } from "zod";
import { getParquetLayerDay } from "@/lib/server/services/parquet-plane-client";
import {
  boundedResult,
  commonRequest,
  daySchema,
  finiteNumberSchema,
  instantSchema,
  mapEnvelope,
  parseRows,
  rejectFutureDay,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

const sensorRowSchema = z
  .object({
    sensor_id: z.string().nullable(),
    station_name: z.string().nullable(),
    network: z.string().nullable(),
    observed_day: daySchema,
    observed_at: instantSchema,
    measurement_name: z.string().min(1),
    value: finiteNumberSchema,
    unit_code: z.string().nullable(),
    quality_control: z.string().nullable(),
    feature_id: z.string().nullable(),
    data_available_at: instantSchema.nullable(),
    station_longitude: finiteNumberSchema.nullable(),
    station_latitude: finiteNumberSchema.nullable(),
  })
  .strict();

/**
 * One station-day, collapsed from the lane's tall one-row-per-measurement grain.
 *
 * See `src/lib/server/services/AGENTS.md` §sensors.
 */
export interface ParquetSensorStation {
  sensorId: string | null;
  stationName: string | null;
  network: string | null;
  observedDay: string;
  observedAt: string;
  longitude: number;
  latitude: number;
  /** Every measurement this station reported on the served day, newest reading per name. */
  measurements: readonly {
    name: string;
    value: number;
    unitCode: string | null;
    observedAt: string;
  }[];
}

/** One station per merge key, carrying every measurement it reported and its newest reading. */
function collapseSensorRows(
  rows: readonly z.infer<typeof sensorRowSchema>[]
): ParquetSensorStation[] {
  const stations = new Map<string, ParquetSensorStation>();
  for (const row of rows) {
    // A row with no coordinates cannot be drawn, and never was. Dropped rather than plotted at a
    // fabricated origin.
    if (row.station_longitude === null || row.station_latitude === null) continue;
    // At a coarse rung `sensor_id` is null by construction, so the cell's coordinates are the key.
    const key = row.sensor_id ?? `${row.station_longitude}:${row.station_latitude}`;
    const existing = stations.get(key);
    const measurement = {
      name: row.measurement_name,
      value: row.value,
      unitCode: row.unit_code,
      observedAt: row.observed_at,
    };
    if (existing === undefined) {
      stations.set(key, {
        sensorId: row.sensor_id,
        stationName: row.station_name,
        network: row.network,
        observedDay: row.observed_day,
        observedAt: row.observed_at,
        longitude: row.station_longitude,
        latitude: row.station_latitude,
        measurements: [measurement],
      });
      continue;
    }
    stations.set(key, {
      ...existing,
      // The station's own timestamp is the NEWEST reading it filed that day.
      observedAt:
        Date.parse(row.observed_at) > Date.parse(existing.observedAt)
          ? row.observed_at
          : existing.observedAt,
      network: existing.network ?? row.network,
      stationName: existing.stationName ?? row.station_name,
      measurements: [...existing.measurements, measurement],
    });
  }
  return [...stations.values()];
}

/** The published sensor roster for one day, one feature per station rather than per measurement. */
export async function getParquetSensorStations(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetSensorStation[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "sensors");
  rejectFutureDay(day, nowMs, "sensors");
  return boundedResult(async () =>
    mapEnvelope(await getParquetLayerDay(request), (rows) =>
      collapseSensorRows(parseRows(rows, sensorRowSchema, "sensors"))
    )
  );
}
