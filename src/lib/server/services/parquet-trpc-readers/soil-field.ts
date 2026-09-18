import {
  SOIL_FIELD_ATTRIBUTION,
  SOIL_FIELD_SOURCE_KEY,
  SOIL_FIELD_SUPPORT_KEY,
  soilFieldBandFor,
  soilFieldDepthDefinition,
  soilFieldMeasureDefinition,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import {
  BASE_ZOOM_TIER,
  LANE_BASE_LATTICES,
  resolveZoomTier,
  servedCellLattice,
  tessellatedCellPolygon,
  type ServedCellLattice,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import { granularityForZoomTier } from "@/lib/server/services/zoom-granularity";
import {
  type PublishedSoilFieldCollection,
  type SoilFieldFeatureProperties,
  type SoilFieldReadOptions,
} from "@/lib/server/services/environmental-contracts";
import { SOIL_FIELD_MAX_CELLS } from "@/lib/server/services/parquet-layer-constants";
import { getParquetLayerDay } from "@/lib/server/services/parquet-plane-client";
import {
  contractError,
  currentUtcDay,
  LANE_ATTRIBUTIONS,
  parseRows,
  selectedDay,
} from "./shared";
import {
  climateSnapshotLineageRowSchema,
  signalPlaneRowSchema,
  soilTemperatureRowSchema,
  SNAPSHOT_SOURCE_MANIFEST_SHA256,
  type SoilServingRow,
} from "./signal-plane-rows";

const SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS = 0;

const SOIL_FIELD_LANES = {
  moisture: {
    surface: "soil-field-moisture-0-7cm",
    "root-zone": "soil-field-moisture-7-28cm",
    deep: "soil-field-moisture-28-100cm",
  },
  temperature: {
    surface: "soil-temperature-0-to-7cm",
    "root-zone": "soil-temperature-7-to-28cm",
    deep: "soil-temperature-28-to-100cm",
    substratum: "soil-temperature-100-to-255cm",
  },
  vpd: { surface: "soil-field-vpd" },
} as const;

const SOIL_TEMPERATURE_SOURCE_PARAMETERS = {
  surface: "soil_temperature_0_to_7cm_mean",
  "root-zone": "soil_temperature_7_to_28cm_mean",
  deep: "soil_temperature_28_to_100cm_mean",
  substratum: "soil_temperature_100_to_255cm_mean",
} as const satisfies Readonly<Record<SoilFieldDepth, string>>;

const SOIL_MOISTURE_SOURCE_PARAMETERS = {
  surface: "soil_moisture_0_to_7cm_mean",
  "root-zone": "soil_moisture_7_to_28cm_mean",
  deep: "soil_moisture_28_to_100cm_mean",
} as const satisfies Readonly<Record<Exclude<SoilFieldDepth, "substratum">, string>>;

/**
 * A soil-field collection that also declares WHICH rung answered it and what its cells stand for.
 *
 * See `src/lib/server/services/AGENTS.md` §soil-field.
 */
export interface ZoomedSoilFieldCollection extends PublishedSoilFieldCollection {
  /** The one physical rung the rows came from; exactly one per request. */
  zoomTier: ZoomTier;
  /** ONE envelope for the whole collection, not one per feature. */
  support: AggregateEnvelopeSupport;
}

/**
 * What a whole soil-field answer's cells stand for: one lane, one day, one rung.
 *
 * `supportId` names the LATTICE rather than a cell, which is what makes it stable across pans of
 * the same request and therefore usable as a cache identity.
 */
function soilFieldSupport(
  layer: string,
  zoomTier: ZoomTier,
  observedDay: string,
  rows: readonly SoilServingRow[]
): AggregateEnvelopeSupport {
  const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES["soil-field"]);
  const newestObservedAt = rows.reduce<string | null>(
    (newest, row) =>
      newest === null || Date.parse(row.newest_observed_at) > Date.parse(newest)
        ? row.newest_observed_at
        : newest,
    null
  );
  return {
    zoomTier,
    supportKind: "tessellated_cell",
    supportId: `${layer}:${observedDay}:z${zoomTier}`,
    origin: lattice.origin,
    cellWidthDegrees: lattice.cellSizeDegrees,
    cellHeightDegrees: lattice.cellSizeDegrees,
    aggregationMethod: "mean",
    contributorCount: rows.reduce((total, row) => total + row.observation_count, 0),
    provenance: {
      sourceLayer: layer,
      observedDay,
      newestObservedAt,
      attribution: LANE_ATTRIBUTIONS["soil-field"],
    },
  };
}

function soilFieldLane(measure: SoilFieldMeasure, depth: SoilFieldDepth): string {
  if (measure === "moisture") {
    if (depth === "root-zone") return SOIL_FIELD_LANES.moisture["root-zone"];
    if (depth === "deep") return SOIL_FIELD_LANES.moisture.deep;
    return SOIL_FIELD_LANES.moisture.surface;
  }
  if (measure === "temperature") {
    return SOIL_FIELD_LANES.temperature[depth];
  }
  return SOIL_FIELD_LANES.vpd.surface;
}

function emptyParquetSoilField(
  reason: NonNullable<PublishedSoilFieldCollection["reason"]>,
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth,
  layer: string,
  requestedDay: string,
  zoomTier: ZoomTier,
  newestAvailableDay: string | null
): ZoomedSoilFieldCollection {
  const definition = soilFieldMeasureDefinition(measure);
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    reason,
    granularity: granularityForZoomTier(zoomTier),
    measure,
    depth,
    unit: definition.unit,
    attribution: SOIL_FIELD_ATTRIBUTION,
    observedDay: null,
    requestedDay,
    newestAvailableDay,
    cellCount: 0,
    truncated: false,
    maxCellCount: SOIL_FIELD_MAX_CELLS,
    maxObservationAgeDays: SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS,
    latticeDegrees: servedCellLattice(zoomTier, LANE_BASE_LATTICES["soil-field"]).cellSizeDegrees,
    smoothingSigmaDegrees: null,
    bands: definition.bands,
    sourceClientExposureApproved: false,
    zoomTier,
    // Declared even when nothing was drawn: an empty collection still has to say which rung was
    // asked and at what pitch, or the panel cannot tell "no cells here" from "no rung answered".
    support: soilFieldSupport(layer, zoomTier, requestedDay, []),
  };
}

// See services/AGENTS.md §soil-direct-lineage for the writer ownership boundary and coarse-row limitation.
function soilLineageMatches(
  row: Record<string, unknown>,
  measure: SoilFieldMeasure,
  servedDay: string,
  zoomTier: ZoomTier
): boolean {
  const frozenDay = servedDay <= "2026-08-02";
  if (measure === "moisture") {
    if (frozenDay) {
      return (
        row.source_manifest_sha256 === SNAPSHOT_SOURCE_MANIFEST_SHA256 &&
        row.source_snapshot_id === "prod-20260826-full-signal-v1"
      );
    }
    const directReleaseId = `direct:${String(row.source_manifest_sha256)}`;
    return (
      row.source_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256 &&
      row.source_snapshot_id === directReleaseId &&
      row.precedence_contract === "open-meteo-era5-land-archive-per-support-cell-v1" &&
      (zoomTier === BASE_ZOOM_TIER
        ? row.selected_source_release_id === directReleaseId &&
          row.selected_source_release_payload_checksum === row.source_manifest_sha256
        : row.selected_source_release_id === null)
    );
  }
  if (measure === "temperature") {
    if (frozenDay) {
      return (
        row.input_manifest_sha256 === SNAPSHOT_SOURCE_MANIFEST_SHA256 &&
        !(
          typeof row.selected_source_release_id === "string" &&
          row.selected_source_release_id.startsWith("direct:")
        )
      );
    }
    return (
      row.input_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256 &&
      (zoomTier === BASE_ZOOM_TIER
        ? row.selected_source_release_id === `direct:${String(row.input_manifest_sha256)}`
        : row.selected_source_release_id === null)
    );
  }
  return true;
}

function decodeSoilFieldRows(
  rows: readonly Record<string, unknown>[],
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth,
  signalName: string,
  unit: string,
  layer: string,
  servedDay: string,
  zoomTier: ZoomTier
): SoilServingRow[] {
  const parsed =
    measure === "moisture"
      ? parseRows(rows, climateSnapshotLineageRowSchema, layer)
      : measure === "temperature"
        ? parseRows(rows, soilTemperatureRowSchema, layer)
        : parseRows(rows, signalPlaneRowSchema, layer);
  const seenCells = new Set<string>();
  return parsed.map((row) => {
    if (
      row.support_key !== SOIL_FIELD_SUPPORT_KEY ||
      row.signal_name !== signalName ||
      row.normalized_unit !== unit ||
      row.observed_day !== servedDay ||
      (measure === "moisture" &&
        (!("source_key" in row) ||
          row.source_key !== SOIL_FIELD_SOURCE_KEY ||
          depth === "substratum" ||
          !("source_parameter" in row) ||
          row.source_parameter !== SOIL_MOISTURE_SOURCE_PARAMETERS[depth] ||
          !soilLineageMatches(row, measure, servedDay, zoomTier))) ||
      (measure === "temperature" &&
        (!("data_source_key" in row) ||
          row.data_source_key !== SOIL_FIELD_SOURCE_KEY ||
          !("source_parameter" in row) ||
          row.source_parameter !== SOIL_TEMPERATURE_SOURCE_PARAMETERS[depth] ||
          !soilLineageMatches(row, measure, servedDay, zoomTier)))
    ) {
      throw contractError(`${layer} returned a row outside its registered soil-field contract`);
    }
    if ((zoomTier === BASE_ZOOM_TIER) !== (row.cell_id !== null)) {
      throw contractError(`${layer} returned invalid cell identity nullability at z${zoomTier}`);
    }
    if (
      row.cell_longitude < -180 ||
      row.cell_longitude > 180 ||
      row.cell_latitude < -90 ||
      row.cell_latitude > 90
    ) {
      throw contractError(`${layer} returned a cell outside WGS84 bounds`);
    }
    const cellKey = row.cell_id ?? `${row.cell_longitude}:${row.cell_latitude}`;
    if (seenCells.has(cellKey)) {
      throw contractError(`${layer} returned duplicate cell ${cellKey} for ${servedDay}`);
    }
    seenCells.add(cellKey);
    return row;
  });
}

/**
 * The complete tessellating cell one served soil row stands for.
 *
 * Corners come from the lattice index rather than the row's own float, so two neighbours' shared
 * edge is the same double. See `src/lib/server/services/AGENTS.md` §tessellated-support-geometry.
 */
function soilFieldPolygon(row: SoilServingRow, lattice: ServedCellLattice): GeoJSON.Polygon {
  return tessellatedCellPolygon(row.cell_longitude, row.cell_latitude, lattice);
}

/**
 * Read one soil-field viewport exclusively from its registered Parquet product lane.
 *
 * The cancellation is an intersection on the parameter rather than a field on
 * `SoilFieldReadOptions`; see `src/lib/server/services/AGENTS.md` §request-cancellation.
 */
export async function getParquetSoilField(
  bbox: string,
  options: SoilFieldReadOptions & { signal?: AbortSignal } = {}
): Promise<ZoomedSoilFieldCollection> {
  const measure = options.measure ?? "moisture";
  const definition = soilFieldMeasureDefinition(measure);
  const requestedDepth = options.depth ?? definition.defaultDepth;
  const { depth, signalName } = soilFieldDepthDefinition(measure, requestedDepth);
  const requestedDay = selectedDay(options.date, undefined);
  const zoomTier = resolveZoomTier(options.zoom ?? BASE_ZOOM_TIER);
  const layer = soilFieldLane(measure, depth);
  if (requestedDay > currentUtcDay()) {
    return emptyParquetSoilField(
      "not_forecastable",
      measure,
      depth,
      layer,
      requestedDay,
      zoomTier,
      null
    );
  }
  const envelope = await getParquetLayerDay({
    layer,
    day: requestedDay,
    zoomTier,
    bbox,
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  if (envelope.state !== "published") {
    return emptyParquetSoilField(
      "not_published",
      measure,
      depth,
      layer,
      requestedDay,
      zoomTier,
      null
    );
  }
  if (envelope.servedDay !== requestedDay) {
    throw contractError(`${layer} served ${envelope.servedDay} for exact day ${requestedDay}`);
  }

  const rows = decodeSoilFieldRows(
    envelope.rows,
    measure,
    depth,
    signalName,
    definition.unit,
    layer,
    envelope.servedDay,
    zoomTier
  );
  const drawable = rows.slice(0, SOIL_FIELD_MAX_CELLS);
  const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES["soil-field"]);
  const features = drawable.map((row): GeoJSON.Feature<GeoJSON.Polygon> => {
    const band = soilFieldBandFor(measure, row.normalized_value);
    const properties: SoilFieldFeatureProperties = {
      value: row.normalized_value,
      bandIndex: band.bandIndex,
      bandLabel: band.label,
      aggregated: zoomTier !== BASE_ZOOM_TIER,
      cellKey: row.cell_id,
      coverageFraction: row.coverage_fraction,
    };
    return {
      type: "Feature",
      id: row.cell_id ?? `${layer}:${envelope.servedDay}:${row.cell_longitude}:${row.cell_latitude}`,
      geometry: soilFieldPolygon(row, lattice),
      properties,
    };
  });
  if (features.length === 0) {
    return emptyParquetSoilField(
      "not_published",
      measure,
      depth,
      layer,
      requestedDay,
      zoomTier,
      envelope.servedDay
    );
  }
  return {
    type: "FeatureCollection",
    features,
    availability: "published",
    reason: null,
    granularity: granularityForZoomTier(zoomTier),
    measure,
    depth,
    unit: definition.unit,
    attribution: SOIL_FIELD_ATTRIBUTION,
    observedDay: envelope.servedDay,
    requestedDay,
    newestAvailableDay: null,
    cellCount: features.length,
    truncated: envelope.truncated || rows.length > SOIL_FIELD_MAX_CELLS,
    maxCellCount: SOIL_FIELD_MAX_CELLS,
    maxObservationAgeDays: SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS,
    latticeDegrees: lattice.cellSizeDegrees,
    smoothingSigmaDegrees: null,
    bands: definition.bands,
    sourceClientExposureApproved: rows.every(
      (row) => row.allowed_client_exposure === true
    ),
    zoomTier,
    support: soilFieldSupport(layer, zoomTier, envelope.servedDay, drawable),
  };
}
