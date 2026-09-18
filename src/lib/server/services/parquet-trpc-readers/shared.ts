import type { MtbsSnapshotMetadata } from "@/lib/environmental/mtbs-snapshot";
import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { CLIMATE_FIELD_ATTRIBUTION } from "@/lib/environmental/climate-field";
import { SOIL_FIELD_ATTRIBUTION } from "@/lib/environmental/soil-field";
import type {
  AggregateEnvelopeSupport,
  AggregationMethod,
  SupportKind,
} from "@/lib/map/layer-render-contract";
import {
  LANE_BASE_LATTICES,
  latticeCellIndex,
  latticeCellSpan,
  mintedSupportId,
  resolveZoomTier,
  servedCellLattice,
  type CellLaneId,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import {
  UpstreamAbortedError,
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import {
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";
import {
  assertExhaustiveParquetPlaneState,
  type GovernedAbsenceEvidence,
  type ParquetPlaneEnvelope,
} from "@/lib/server/services/parquet-envelope";

export const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
export const DAY_MS = 86_400_000;

/** Attribution that must be shown wherever each lane's values are drawn; see services/AGENTS.md §lane-attributions. */
export const LANE_ATTRIBUTIONS = {
  "fire-detections": "NASA FIRMS (LANCE/ESDIS)",
  "water-gauges": "U.S. Geological Survey NWIS",
  "weather-observations": "Open-Meteo",
  vegetation: "Copernicus Sentinel-2 surface reflectance",
  "climate-field": CLIMATE_FIELD_ATTRIBUTION,
  "soil-field": SOIL_FIELD_ATTRIBUTION,
} as const satisfies Readonly<Record<CellLaneId, string>>;

/** Everything one row knows about itself that the envelope cannot derive from the ladder. */
export interface CellSupportInput {
  lane: CellLaneId;
  /** The exact Parquet product read, when the lane publishes several; defaults to the lane. */
  sourceLayer?: string;
  zoomTier: ZoomTier;
  supportKind: SupportKind;
  aggregationMethod: AggregationMethod;
  /** Source observations or features behind this envelope. Read off a column, never inferred. */
  contributorCount: number;
  /** The warehouse's own identity for the cell, or null on a rung that publishes none. */
  cellId: string | null;
  longitude: number;
  latitude: number;
  observedDay: string;
  newestObservedAt: string | null;
}

/**
 * The support envelope one served row declares about itself; one builder for every lane.
 *
 * See `src/lib/server/services/AGENTS.md` §tessellated-support-geometry.
 */
export function cellSupport(input: CellSupportInput): AggregateEnvelopeSupport {
  const lattice = servedCellLattice(input.zoomTier, LANE_BASE_LATTICES[input.lane]);
  const cellSize =
    input.supportKind === "raw_point"
      ? {}
      : {
          cellWidthDegrees: lattice.cellSizeDegrees,
          cellHeightDegrees: lattice.cellSizeDegrees,
          // The corner this row's cell was actually snapped to, so the client draws the SAME
          // square rather than re-deriving one from a lattice whose phase it cannot see.
          cellOriginDegrees: [
            latticeCellSpan(latticeCellIndex(input.longitude, lattice), lattice)[0],
            latticeCellSpan(latticeCellIndex(input.latitude, lattice), lattice)[0],
          ] as const,
        };
  return {
    zoomTier: input.zoomTier,
    supportKind: input.supportKind,
    supportId:
      input.cellId ?? mintedSupportId(input.zoomTier, input.longitude, input.latitude),
    // A raw point is centred on its own coordinate; naming a corner would invite a half-cell offset.
    origin: input.supportKind === "raw_point" ? "cell_center" : lattice.origin,
    ...cellSize,
    aggregationMethod: input.aggregationMethod,
    contributorCount: input.contributorCount,
    provenance: {
      sourceLayer: input.sourceLayer ?? input.lane,
      observedDay: input.observedDay,
      newestObservedAt: input.newestObservedAt,
      attribution: LANE_ATTRIBUTIONS[input.lane],
    },
  };
}

/**
 * How a read failed. `aborted` describes the CALLER walking away, not the upstream; see
 * `src/lib/server/services/AGENTS.md` §request-cancellation.
 */
export type ParquetReaderFailureKind =
  | "configuration"
  | "http"
  | "network"
  | "payload"
  | "timeout"
  | "contract"
  | "aborted";

export type ParquetReaderResult<T> =
  | {
      state: "ready";
      requestedDay: string;
      servedDay: string;
      data: T;
      truncated: boolean;
      mtbsSnapshot?: MtbsSnapshotMetadata;
    }
  | {
      state: "absent";
      requestedDay: string;
      servedDay: string;
      evidence: GovernedAbsenceEvidence;
    }
  | {
      state: "not_generated";
      requestedDay: string;
      reason: "day_not_written" | "lane_never_written";
    }
  | {
      state: "upstream_unavailable";
      fault: {
        kind: ParquetReaderFailureKind;
        message: string;
        status?: number;
      };
    };

/**
 * An abandoned read is never an answer: turns an `aborted` fault into a thrown 499.
 *
 * See `src/lib/server/services/AGENTS.md` §request-cancellation.
 */
export function rejectAborted<T>(result: ParquetReaderResult<T>): ParquetReaderResult<T> {
  if (result.state === "upstream_unavailable" && result.fault.kind === "aborted") {
    throw new TRPCError({ code: "CLIENT_CLOSED_REQUEST", message: result.fault.message });
  }
  return result;
}

export interface ParquetViewportRead {
  bbox?: string;
  date?: string;
  mapZoom: number;
  /** Test seam for omitted-day selection; production callers leave it unset. */
  nowMs?: number;
  /**
   * The tRPC resolver's cancellation, threaded down to the socket. `getParquetClimateField` is the
   * one reader that cannot use this name; see `src/lib/server/services/AGENTS.md` §request-cancellation.
   */
  signal?: AbortSignal;
}

export const daySchema = z.string().regex(DAY_PATTERN);
export const instantSchema = z
  .string()
  .endsWith("Z")
  .refine((value) => Number.isFinite(Date.parse(value)), "Expected a UTC instant");
export const finiteNumberSchema = z.number().finite();

const positionSchema = z.tuple([finiteNumberSchema, finiteNumberSchema]).rest(finiteNumberSchema);
const ringSchema = z.array(positionSchema).min(4);
const polygonCoordinatesSchema = z.array(ringSchema).min(1);

/**
 * The two geometry types every native-polygon lane serves; one decoder for all five of them.
 *
 * See `src/lib/server/services/AGENTS.md` §polygon-lanes.
 */
const polygonGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: polygonCoordinatesSchema }).strict(),
  z
    .object({ type: z.literal("MultiPolygon"), coordinates: z.array(polygonCoordinatesSchema).min(1) })
    .strict(),
]);

export type ParquetPolygonGeometry = z.infer<typeof polygonGeometrySchema>;

export function decodePolygonGeometry(geojson: string, lane: string): ParquetPolygonGeometry {
  let rawGeometry: unknown;
  try {
    rawGeometry = JSON.parse(geojson);
  } catch {
    throw contractError(`${lane} geom is not GeoJSON text`);
  }
  const geometry = polygonGeometrySchema.safeParse(rawGeometry);
  if (!geometry.success) throw contractError(`${lane} geom is not a Polygon or MultiPolygon`);
  return geometry.data;
}

export function currentUtcDay(nowMs = Date.now()): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

export function selectedDay(date: string | undefined, nowMs: number | undefined): string {
  const day = date ?? currentUtcDay(nowMs);
  const parsedMs = Date.parse(`${day}T00:00:00Z`);
  if (
    !DAY_PATTERN.test(day) ||
    Number.isNaN(parsedMs) ||
    new Date(parsedMs).toISOString().slice(0, 10) !== day
  ) {
    throw new ParquetPlaneRequestError(`date must be a YYYY-MM-DD calendar day, got "${day}"`);
  }
  return day;
}

export function addUtcDays(day: string, count: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + count * DAY_MS).toISOString().slice(0, 10);
}

export function rejectFutureDay(day: string, nowMs: number, layer: string): void {
  const today = currentUtcDay(nowMs);
  if (day > today) {
    throw new ParquetPlaneRequestError(
      `${layer} cannot read future day ${day}; server UTC today is ${today}`
    );
  }
}

export function envelopeDayMs(day: string, field: string): number {
  const parsedMs = Date.parse(`${day}T00:00:00Z`);
  if (
    !DAY_PATTERN.test(day) ||
    Number.isNaN(parsedMs) ||
    new Date(parsedMs).toISOString().slice(0, 10) !== day
  ) {
    throw contractError(`Parquet plane returned an invalid ${field} calendar day "${day}"`);
  }
  return parsedMs;
}

export function contractError(message: string): ParquetPlaneContractError {
  return new ParquetPlaneContractError(message);
}

export function parseRows<T>(
  rows: readonly Record<string, unknown>[],
  schema: z.ZodType<T>,
  layer: string
): T[] {
  const parsed = z.array(schema).safeParse(rows);
  if (!parsed.success) {
    throw contractError(`${layer} rows do not match the registered Parquet schema`);
  }
  return parsed.data;
}

export function newestByKey<T>(
  rows: readonly T[],
  keyFor: (row: T) => string,
  observedAtFor: (row: T) => string
): T[] {
  const newest = new Map<string, T>();
  for (const row of rows) {
    const key = keyFor(row);
    const previous = newest.get(key);
    if (
      previous === undefined ||
      Date.parse(observedAtFor(row)) > Date.parse(observedAtFor(previous))
    ) {
      newest.set(key, row);
    }
  }
  return [...newest.values()];
}

export function mapEnvelope<T>(
  envelope: ParquetPlaneEnvelope,
  decodeRows: (rows: readonly Record<string, unknown>[]) => T
): ParquetReaderResult<T> {
  switch (envelope.state) {
    case "published":
      return {
        state: "ready",
        requestedDay: envelope.requestedDay,
        servedDay: envelope.servedDay,
        data: decodeRows(envelope.rows),
        truncated: envelope.truncated,
      };
    case "governed_absence":
      return {
        state: "absent",
        requestedDay: envelope.requestedDay,
        servedDay: envelope.servedDay,
        evidence: envelope.evidence,
      };
    case "day_not_written":
      return {
        state: "not_generated",
        requestedDay: envelope.requestedDay,
        reason: "day_not_written",
      };
    case "lane_never_written":
      return {
        state: "not_generated",
        requestedDay: envelope.requestedDay,
        reason: "lane_never_written",
      };
    default:
      return assertExhaustiveParquetPlaneState(envelope);
  }
}

type ParquetUpstreamFailure = Extract<
  ParquetReaderResult<never>,
  { state: "upstream_unavailable" }
>;

export function parquetUpstreamFailure(error: unknown): ParquetUpstreamFailure | null {
  // First, and ahead of the timeout arm: an abort and a timeout are the same DOMException on the
  // wire, and calling a client that navigated away a service outage would page someone for it.
  if (error instanceof UpstreamAbortedError) {
    return { state: "upstream_unavailable", fault: { kind: "aborted", message: error.message } };
  }
  if (error instanceof UpstreamConfigurationError) {
    return { state: "upstream_unavailable", fault: { kind: "configuration", message: error.message } };
  }
  if (error instanceof UpstreamHttpError) {
    return {
      state: "upstream_unavailable",
      fault: { kind: "http", message: error.message, status: error.status },
    };
  }
  if (error instanceof UpstreamPayloadError) {
    return { state: "upstream_unavailable", fault: { kind: "payload", message: error.message } };
  }
  if (error instanceof UpstreamTimeoutError) {
    return { state: "upstream_unavailable", fault: { kind: "timeout", message: error.message } };
  }
  if (
    error instanceof TypeError &&
    /(?:fetch failed|failed to fetch|networkerror|network request failed)/i.test(error.message)
  ) {
    return { state: "upstream_unavailable", fault: { kind: "network", message: error.message } };
  }
  if (error instanceof ParquetPlaneContractError) {
    return { state: "upstream_unavailable", fault: { kind: "contract", message: error.message } };
  }
  return null;
}

export async function boundedResult<T>(
  work: () => Promise<ParquetReaderResult<T>>
): Promise<ParquetReaderResult<T>> {
  try {
    return await work();
  } catch (error) {
    const failure = parquetUpstreamFailure(error);
    if (failure !== null) return failure;
    throw error;
  }
}

export function commonRequest(input: ParquetViewportRead, layer: string) {
  const day = selectedDay(input.date, input.nowMs);
  const zoomTier = resolveZoomTier(input.mapZoom);
  return {
    day,
    request: {
      layer,
      day,
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    },
  };
}

/** The terminal non-ready state of a multi-day window: the newest missing day, else the newest absence. */
export function windowTerminal<T>(
  days: readonly ParquetReaderResult<T>[],
  firstDay: string,
  lastDay: string
): Exclude<ParquetReaderResult<never>, { state: "ready" | "upstream_unavailable" }> {
  const missing = [...days]
    .reverse()
    .find((day): day is Extract<ParquetReaderResult<T>, { state: "not_generated" }> => day.state === "not_generated");
  if (missing !== undefined) return missing;
  const absent = [...days]
    .reverse()
    .find((day): day is Extract<ParquetReaderResult<T>, { state: "absent" }> => day.state === "absent");
  if (absent !== undefined) return absent;
  throw contractError(`Parquet plane described no state in the window ${firstDay}..${lastDay}`);
}
