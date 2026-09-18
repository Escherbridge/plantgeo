import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import {
  climateFieldSignalDefinition,
  climateFieldSignalName,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import { BASE_ZOOM_TIER, resolveZoomTier, type ZoomTier } from "@/lib/map/zoom-tiers";
import { getParquetLayerDay } from "@/lib/server/services/parquet-plane-client";
import {
  boundedResult,
  cellSupport,
  contractError,
  mapEnvelope,
  parseRows,
  selectedDay,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";
import {
  climateSnapshotLineageRowSchema,
  signalPlaneRowSchema,
  soilWetnessRowSchema,
  SNAPSHOT_SOURCE_MANIFEST_SHA256,
} from "./signal-plane-rows";

export const PARQUET_CLIMATE_FIELD_SIGNAL_IDS = [
  "air-temperature",
  "dew-point",
  "precipitation",
  "relative-humidity",
  "shortwave-radiation",
  "wind-speed",
  "soil-wetness-surface",
  "soil-wetness-root-zone",
  "soil-wetness-profile",
] as const satisfies readonly ClimateFieldSignalId[];

export type ParquetClimateFieldSignalId =
  (typeof PARQUET_CLIMATE_FIELD_SIGNAL_IDS)[number];

const PARQUET_CLIMATE_FIELD_SIGNAL_SET = new Set<ClimateFieldSignalId>(
  PARQUET_CLIMATE_FIELD_SIGNAL_IDS
);

const STANDARD_CLIMATE_FIELD_LANES = {
  "air-temperature": {
    layer: null,
    rowContract: "signal-plane",
    sourceParameter: null,
    directFirstDay: null,
  },
  "dew-point": {
    layer: "climate-field-dew-point",
    rowContract: "signal-plane",
    sourceParameter: null,
    directFirstDay: null,
  },
  precipitation: {
    layer: "climate-field-precipitation",
    rowContract: "snapshot-lineage",
    sourceParameter: "PRECTOTCORR",
    directFirstDay: "2026-08-07",
  },
  "relative-humidity": {
    layer: "climate-field-relative-humidity",
    rowContract: "snapshot-lineage",
    sourceParameter: "RH2M",
    directFirstDay: "2026-08-07",
  },
  "shortwave-radiation": {
    layer: "climate-field-shortwave-radiation",
    rowContract: "snapshot-lineage",
    sourceParameter: "ALLSKY_SFC_SW_DWN",
    directFirstDay: "2026-06-01",
  },
  "wind-speed": {
    layer: "climate-field-wind-speed",
    rowContract: "signal-plane",
    sourceParameter: null,
    directFirstDay: null,
  },
  "soil-wetness-surface": {
    layer: "soil-wetness-surface",
    rowContract: "soil-wetness",
    sourceParameter: null,
    directFirstDay: null,
  },
  "soil-wetness-root-zone": {
    layer: "soil-wetness-root-zone",
    rowContract: "soil-wetness",
    sourceParameter: null,
    directFirstDay: null,
  },
  "soil-wetness-profile": {
    layer: "soil-wetness-profile",
    rowContract: "soil-wetness",
    sourceParameter: null,
    directFirstDay: null,
  },
} as const satisfies Record<
  ClimateFieldSignalId,
  {
    layer: string | null;
    rowContract: "signal-plane" | "snapshot-lineage" | "soil-wetness";
    sourceParameter: string | null;
    directFirstDay: string | null;
  }
>;

export interface ParquetClimateFieldObservation {
  /** The stored cell's identity at z13; null on the coarse rungs, whose cells are anonymous aggregates. */
  cellId: string | null;
  observedDay: string;
  value: number;
  observationCount: number;
  newestObservedAt: string;
  coverageFraction: number | null;
  allowedClientExposure: boolean | null;
  longitude: number;
  latitude: number;
  /** The lattice cell this value describes; `tessellated_cell` at every rung. */
  support: AggregateEnvelopeSupport;
}

export function isParquetClimateFieldSignal(
  signal: ClimateFieldSignalId
): signal is ParquetClimateFieldSignalId {
  return PARQUET_CLIMATE_FIELD_SIGNAL_SET.has(signal);
}

function climateFieldProduct(
  signal: ParquetClimateFieldSignalId,
  variant: AirTemperatureVariant
) {
  const contract = STANDARD_CLIMATE_FIELD_LANES[signal];
  const layer =
    signal === "air-temperature"
      ? `climate-field-air-temperature-${variant}`
      : contract.layer;
  if (layer === null) {
    throw contractError(`${signal} has no registered Parquet product layer`);
  }
  return {
    ...contract,
    layer,
  };
}

/** Validate immutable-snapshot or source-direct lineage for a climate snapshot product. */
function climateSnapshotLineageMatches(
  row: Record<string, unknown>,
  servedDay: string,
  zoomTier: ZoomTier,
  directFirstDay: string | null
): boolean {
  if (row.source_manifest_sha256 === SNAPSHOT_SOURCE_MANIFEST_SHA256) {
    return directFirstDay === null || servedDay < directFirstDay;
  }
  const directReleaseId = `direct:${String(row.source_manifest_sha256)}`;
  const baseOnlyColumns = [
    "selected_source_row_id",
    "selected_source_row_sha256",
    "selected_source_release_id",
    "selected_source_release_retrieved_at",
    "selected_source_release_payload_checksum",
    "selected_source_part_key",
    "selected_source_part_sha256",
    "selected_source_row_ordinal",
    "input_source_row_digest",
    "input_source_row_ids",
    "input_source_row_sha256s",
    "input_source_release_ids",
    "input_source_part_keys",
    "input_source_part_sha256s",
    "input_source_row_ordinals",
  ] as const;
  if (
    directFirstDay === null ||
    servedDay < directFirstDay ||
    row.source_snapshot_id !== directReleaseId ||
    row.precedence_contract !== "nasa-power-point-per-support-cell-v1"
  ) {
    return false;
  }
  if (zoomTier !== BASE_ZOOM_TIER) {
    return (
      typeof row.input_source_row_count === "number" &&
      row.input_source_row_count > 0 &&
      baseOnlyColumns.every((column) => row[column] === null)
    );
  }
  const selectedRowId = row.selected_source_row_id;
  const selectedRowSha256 = row.selected_source_row_sha256;
  const selectedPartKey = row.selected_source_part_key;
  const selectedPartSha256 = row.selected_source_part_sha256;
  const selectedRowOrdinal = row.selected_source_row_ordinal;
  return (
    row.input_source_row_count === 1 &&
    typeof selectedRowId === "number" &&
    typeof selectedRowSha256 === "string" &&
    typeof selectedPartKey === "string" &&
    typeof selectedPartSha256 === "string" &&
    selectedRowId === selectedRowOrdinal &&
    row.selected_source_release_id === directReleaseId &&
    row.selected_source_release_payload_checksum === row.source_manifest_sha256 &&
    row.input_source_row_digest === selectedRowSha256 &&
    Array.isArray(row.input_source_row_ids) && row.input_source_row_ids.length === 1 && row.input_source_row_ids[0] === selectedRowId &&
    Array.isArray(row.input_source_row_sha256s) && row.input_source_row_sha256s.length === 1 && row.input_source_row_sha256s[0] === selectedRowSha256 &&
    Array.isArray(row.input_source_release_ids) && row.input_source_release_ids.length === 1 && row.input_source_release_ids[0] === directReleaseId &&
    Array.isArray(row.input_source_part_keys) && row.input_source_part_keys.length === 1 && row.input_source_part_keys[0] === selectedPartKey &&
    Array.isArray(row.input_source_part_sha256s) && row.input_source_part_sha256s.length === 1 && row.input_source_part_sha256s[0] === selectedPartSha256 &&
    Array.isArray(row.input_source_row_ordinals) && row.input_source_row_ordinals.length === 1 && row.input_source_row_ordinals[0] === selectedRowOrdinal &&
    row.selected_source_release_retrieved_at !== null
  );
}

function decodeClimateFieldRows(
  rows: readonly Record<string, unknown>[],
  signal: ParquetClimateFieldSignalId,
  variant: AirTemperatureVariant,
  servedDay: string,
  zoomTier: ZoomTier
): ParquetClimateFieldObservation[] {
  const contract = climateFieldProduct(signal, variant);
  const { layer } = contract;
  const expectedSignalName = climateFieldSignalName(signal, variant);
  const expectedUnit = climateFieldSignalDefinition(signal).unit;
  const seenCells = new Set<string>();
  const parsed =
    contract.rowContract === "snapshot-lineage"
      ? parseRows(rows, climateSnapshotLineageRowSchema, layer)
      : contract.rowContract === "soil-wetness"
        ? parseRows(rows, soilWetnessRowSchema, layer)
        : parseRows(rows, signalPlaneRowSchema, layer);
  return parsed.map((row) => {
    if (
      row.support_key !== "surface" ||
      row.signal_name !== expectedSignalName ||
      row.normalized_unit !== expectedUnit ||
      row.observed_day !== servedDay
    ) {
      throw contractError(`${layer} returned a row outside its registered climate contract`);
    }
    // Reader-side integrity check only: the detail rung carries a stored cell identity and the
    // coarse rungs carry an anonymous aggregate. See services/AGENTS.md §cell-identity-nullability.
    if ((zoomTier === BASE_ZOOM_TIER) !== (row.cell_id !== null)) {
      throw contractError(`${layer} returned invalid cell identity nullability at z${zoomTier}`);
    }
    if (
      contract.rowContract === "snapshot-lineage" &&
      (contract.sourceParameter === null ||
        !("source_key" in row) ||
        row.source_key !== "nasa-power-daily" ||
        !("source_parameter" in row) ||
        row.source_parameter !== contract.sourceParameter ||
        !climateSnapshotLineageMatches(row, servedDay, zoomTier, contract.directFirstDay))
    ) {
      throw contractError(`${layer} returned a row outside its pinned source contract`);
    }
    if (
      contract.rowContract === "soil-wetness" &&
      (!("input_manifest_sha256" in row) ||
        row.input_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)
    ) {
      throw contractError(`${layer} returned a row outside its pinned source contract`);
    }
    if (
      row.cell_longitude < -180 ||
      row.cell_longitude > 180 ||
      row.cell_latitude < -90 ||
      row.cell_latitude > 90
    ) {
      throw contractError(`${layer} returned a cell outside WGS84 bounds`);
    }
    // Keyed on the coordinate pair where there is no identity, so the duplicate check survives the
    // coarse rungs instead of silently passing on a set of nulls.
    const cellKey = row.cell_id ?? `${row.cell_longitude}:${row.cell_latitude}`;
    if (seenCells.has(cellKey)) {
      throw contractError(`${layer} returned duplicate cell ${cellKey} for ${servedDay}`);
    }
    seenCells.add(cellKey);
    return {
      cellId: row.cell_id,
      observedDay: row.observed_day,
      value: row.normalized_value,
      observationCount: row.observation_count,
      newestObservedAt: row.newest_observed_at,
      coverageFraction: row.coverage_fraction,
      allowedClientExposure: row.allowed_client_exposure,
      longitude: row.cell_longitude,
      latitude: row.cell_latitude,
      support: cellSupport({
        lane: "climate-field",
        sourceLayer: layer,
        zoomTier,
        supportKind: "tessellated_cell",
        aggregationMethod: "mean",
        contributorCount: row.observation_count,
        cellId: row.cell_id,
        longitude: row.cell_longitude,
        latitude: row.cell_latitude,
        observedDay: row.observed_day,
        newestObservedAt: row.newest_observed_at,
      }),
    };
  });
}

/**
 * One climate day, and the ONE physical rung that answered it.
 *
 * The tier travels beside the result rather than inside its `ready` arm because every state needs
 * it; see `src/lib/server/services/AGENTS.md` §climate-field.
 */
export interface ParquetClimateFieldRead {
  zoomTier: ZoomTier;
  result: ParquetReaderResult<readonly ParquetClimateFieldObservation[]>;
}

/**
 * Read one exact published climate day from a promoted, frozen-layout Parquet lane, at the rung
 * that serves the caller's map zoom. Exactly one rung per request.
 *
 * `abortSignal`, not `signal`: this lane's `signal` is already the measured quantity. See
 * `src/lib/server/services/AGENTS.md` §climate-field.
 */
export async function getParquetClimateField(
  input: Omit<ParquetViewportRead, "signal"> & {
    bbox: string;
    signal: ClimateFieldSignalId;
    variant: AirTemperatureVariant;
    abortSignal?: AbortSignal;
  }
): Promise<ParquetClimateFieldRead> {
  const day = selectedDay(input.date, input.nowMs);
  const zoomTier = resolveZoomTier(input.mapZoom);
  if (!isParquetClimateFieldSignal(input.signal)) {
    return {
      zoomTier,
      result: {
        state: "upstream_unavailable",
        fault: {
          kind: "contract",
          message: `No frozen-layout Parquet reader is registered for ${input.signal}`,
        },
      },
    };
  }
  const signal = input.signal;
  const layer = climateFieldProduct(signal, input.variant).layer;
  const result = await boundedResult(async () =>
    mapEnvelope(
      await getParquetLayerDay({
        layer,
        day,
        zoomTier,
        bbox: input.bbox,
        ...(input.abortSignal === undefined ? {} : { signal: input.abortSignal }),
      }),
      (rows) => decodeClimateFieldRows(rows, signal, input.variant, day, zoomTier)
    )
  );
  return { zoomTier, result };
}
