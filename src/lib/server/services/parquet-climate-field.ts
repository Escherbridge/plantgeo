import {
  CLIMATE_FIELD_ATTRIBUTION,
  climateFieldBandFor,
  climateFieldSignalDefinition,
  resolveClimateRenderForm,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import { buildIsobands } from "@/lib/geo/isobands";
import {
  CLIMATE_FIELD_MAX_CELLS,
  type ClimateFieldFeatureProperties,
  type PublishedClimateFieldCollection,
} from "@/lib/server/services/environmental-read-model";
import type {
  ParquetClimateFieldObservation,
  ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";

const CELL_DEGREES = 0.5;
const CELL_HALF_DEGREES = CELL_DEGREES / 2;

type ClimateResult = ParquetReaderResult<readonly ParquetClimateFieldObservation[]>;
type ClimateLatticeRow = readonly [
  latitude: number,
  minimumLongitude: number,
  maximumLongitude: number,
  excludedLongitudes?: readonly number[],
];

// Compact form of the frozen 397-cell NASA POWER Western North America lattice.
const CLIMATE_FIELD_LATTICE_ROWS = [
  [31, -116, -104, [-114]],
  [32, -116, -104],
  [33, -117, -104],
  [34, -118, -104],
  [35, -120, -104],
  [36, -121, -104],
  [37, -122, -104],
  [38, -122, -104],
  [39, -123, -104],
  [40, -124, -104],
  [41, -124, -104],
  [42, -124, -104],
  [43, -124, -104],
  [44, -124, -104],
  [45, -124, -104],
  [46, -123, -104],
  [47, -123, -104],
  [48, -124, -104],
  [49, -124, -104, [-123]],
  [50, -124, -104],
  [51, -125, -104],
] as const satisfies readonly ClimateLatticeRow[];

function parseBbox(bbox: string): readonly [number, number, number, number] {
  const bounds = bbox.split(",").map(Number);
  if (
    bounds.length !== 4 ||
    bounds.some((value) => !Number.isFinite(value)) ||
    bounds[0] < -180 ||
    bounds[2] > 180 ||
    bounds[1] < -90 ||
    bounds[3] > 90 ||
    bounds[0] >= bounds[2] ||
    bounds[1] >= bounds[3]
  ) {
    throw new TypeError('Invalid bbox: expected positive WGS84 "west,south,east,north" bounds');
  }
  return bounds as [number, number, number, number];
}

/** Count frozen lattice centers using the private reader's inclusive bbox predicate. */
export function climateFieldLatticeCellCount(bbox: string): number {
  const [west, south, east, north] = parseBbox(bbox);
  let count = 0;
  for (const [latitude, minimumLongitude, maximumLongitude, excluded = []] of
    CLIMATE_FIELD_LATTICE_ROWS) {
    const excludedLongitudes: readonly number[] = excluded;
    if (latitude < south || latitude > north) {
      continue;
    }
    for (let longitude = minimumLongitude; longitude <= maximumLongitude; longitude += 1) {
      if (excludedLongitudes.includes(longitude)) continue;
      if (longitude >= west && longitude <= east) {
        count += 1;
      }
    }
  }
  return count;
}

function cellPolygon(row: ParquetClimateFieldObservation): GeoJSON.Polygon {
  const west = row.longitude - CELL_HALF_DEGREES;
  const east = row.longitude + CELL_HALF_DEGREES;
  const south = row.latitude - CELL_HALF_DEGREES;
  const north = row.latitude + CELL_HALF_DEGREES;
  return {
    type: "Polygon",
    coordinates: [
      [
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
      ],
    ],
  };
}

function emptyCollection(
  signal: ClimateFieldSignalId,
  variant: AirTemperatureVariant,
  renderForm: ClimateRenderForm,
  requestedDay: string,
  latticeCellCount: number
): PublishedClimateFieldCollection {
  const definition = climateFieldSignalDefinition(signal);
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    reason: "not_published",
    granularity: "detail",
    signal,
    variant,
    unit: definition.unit,
    attribution: CLIMATE_FIELD_ATTRIBUTION,
    observedDay: null,
    requestedDay,
    newestAvailableDay: null,
    cellCount: 0,
    latticeCellCount,
    renderForm,
    truncated: false,
    maxCellCount: CLIMATE_FIELD_MAX_CELLS,
    maxObservationAgeDays: 0,
    bands: definition.bands,
    sourceClientExposureApproved: false,
  };
}

function cellFeatures(
  signal: ClimateFieldSignalId,
  renderForm: ClimateRenderForm,
  rows: readonly ParquetClimateFieldObservation[]
): GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon | GeoJSON.Point>[] {
  const definition = climateFieldSignalDefinition(signal);
  if (renderForm === "isoline") {
    const bands = buildIsobands(
      rows.map((row) => ({ lon: row.longitude, lat: row.latitude, value: row.value })),
      CELL_DEGREES,
      [...definition.bandBreaks]
    );
    return bands.flatMap((isoband) => {
      if (isoband.polygons.length === 0) return [];
      const band = definition.bands[isoband.bandIndex];
      if (band === undefined) return [];
      return [{
        type: "Feature" as const,
        id: `${signal}-band-${band.bandIndex}`,
        geometry: isoband.polygons.length === 1
          ? { type: "Polygon" as const, coordinates: isoband.polygons[0] }
          : { type: "MultiPolygon" as const, coordinates: isoband.polygons },
        properties: {
          value: band.representativeValue,
          unit: definition.unit,
          bandIndex: band.bandIndex,
          bandLabel: band.label,
          observedDay: rows[0]?.observedDay ?? "",
          aggregated: true,
          cellKey: null,
          coverageFraction: null,
        } satisfies ClimateFieldFeatureProperties,
      }];
    });
  }

  return rows.map((row) => {
    const band = climateFieldBandFor(signal, row.value);
    return {
      type: "Feature" as const,
      id: row.cellId,
      geometry: renderForm === "symbol"
        ? { type: "Point" as const, coordinates: [row.longitude, row.latitude] }
        : cellPolygon(row),
      properties: {
        value: row.value,
        unit: definition.unit,
        bandIndex: band.bandIndex,
        bandLabel: band.label,
        observedDay: row.observedDay,
        aggregated: false,
        cellKey: row.cellId,
        coverageFraction: row.coverageFraction,
      } satisfies ClimateFieldFeatureProperties,
    };
  });
}

/** Convert a fail-closed Parquet day result into the existing climate GeoJSON contract. */
export function parquetClimateFieldCollection(
  result: Exclude<ClimateResult, { state: "upstream_unavailable" }>,
  signal: ClimateFieldSignalId,
  variant: AirTemperatureVariant,
  bbox: string,
  requestedRenderForm?: ClimateRenderForm
): PublishedClimateFieldCollection {
  const renderForm = resolveClimateRenderForm(signal, requestedRenderForm);
  const latticeCellCount = climateFieldLatticeCellCount(bbox);
  if (result.state !== "ready" || result.data.length === 0) {
    return emptyCollection(
      signal,
      variant,
      renderForm,
      result.requestedDay,
      latticeCellCount
    );
  }
  const definition = climateFieldSignalDefinition(signal);
  const rows = result.data.slice(0, CLIMATE_FIELD_MAX_CELLS);
  const features = cellFeatures(signal, renderForm, rows);
  if (features.length === 0) {
    return emptyCollection(
      signal,
      variant,
      renderForm,
      result.requestedDay,
      latticeCellCount
    );
  }
  return {
    type: "FeatureCollection",
    features,
    availability: "published",
    reason: null,
    granularity: "detail",
    signal,
    variant,
    unit: definition.unit,
    attribution: CLIMATE_FIELD_ATTRIBUTION,
    observedDay: result.servedDay,
    requestedDay: result.requestedDay,
    newestAvailableDay: null,
    cellCount: rows.length,
    latticeCellCount,
    renderForm,
    truncated: result.truncated || result.data.length > CLIMATE_FIELD_MAX_CELLS,
    maxCellCount: CLIMATE_FIELD_MAX_CELLS,
    maxObservationAgeDays: 0,
    bands: definition.bands,
    sourceClientExposureApproved: rows.every((row) => row.allowedClientExposure === true),
  };
}
