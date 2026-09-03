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
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import { granularityForZoomTier } from "@/lib/server/services/zoom-granularity";
import type {
  ParquetClimateFieldObservation,
  ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";

/**
 * The DETAIL rung's lattice pitch, and only that rung's.
 *
 * The coarse rungs aggregate onto their own lattices, whose pitch this module is not told and must
 * not guess: drawing a z0 aggregate as a 0.5-degree square would paint a continent-wide mean as
 * though it were one stored cell. Until the support geometry lands (a later slice), the coarse
 * rungs are drawn as POINTS at the aggregate's own centre -- honest, self-locating, and needing no
 * pitch at all. `CELL_DEGREES` is therefore read only under `zoomTier === 13`.
 */
const CELL_DEGREES = 0.5;
const CELL_HALF_DEGREES = CELL_DEGREES / 2;

/** The rung that serves the producer's own geometry; every other rung is an aggregate. */
const DETAIL_ZOOM_TIER = 13;

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

/**
 * A collection that also declares WHICH rung answered it.
 *
 * Declared here rather than added to `PublishedClimateFieldCollection`: that interface is the
 * PostgreSQL read model's own and is shared with a reader that has no zoom ladder. Extending it
 * locally keeps the tier on every Parquet answer without asserting the older reader publishes one.
 */
export interface ZoomedClimateFieldCollection extends PublishedClimateFieldCollection {
  /** The one physical rung the rows came from; exactly one per request. */
  zoomTier: ZoomTier;
}

/**
 * Only the detail rung may be drawn in the form the client asked for.
 *
 * Squares need the lattice pitch and contours need a regular lattice; a coarse rung has neither
 * yet. Degrading to points is reported back in `renderForm`, so a client cannot mistake the
 * returned geometry for the one it requested.
 */
function tierRenderForm(
  zoomTier: ZoomTier,
  signal: ClimateFieldSignalId,
  requested: ClimateRenderForm | undefined
): ClimateRenderForm {
  return zoomTier === DETAIL_ZOOM_TIER ? resolveClimateRenderForm(signal, requested) : "symbol";
}

function emptyCollection(
  signal: ClimateFieldSignalId,
  variant: AirTemperatureVariant,
  renderForm: ClimateRenderForm,
  requestedDay: string,
  latticeCellCount: number,
  zoomTier: ZoomTier
): ZoomedClimateFieldCollection {
  const definition = climateFieldSignalDefinition(signal);
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    reason: "not_published",
    granularity: granularityForZoomTier(zoomTier),
    zoomTier,
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
      // Coarse rungs carry no cell identity, so the position is the identity -- the same fallback
      // `getParquetSoilField` uses. A feature with no stable id re-enters the map as a new one on
      // every pan and defeats MapLibre's own diffing.
      id: row.cellId ?? `${row.longitude}:${row.latitude}`,
      geometry: renderForm === "symbol"
        ? { type: "Point" as const, coordinates: [row.longitude, row.latitude] }
        : cellPolygon(row),
      properties: {
        value: row.value,
        unit: definition.unit,
        bandIndex: band.bandIndex,
        bandLabel: band.label,
        observedDay: row.observedDay,
        // A null identity IS the aggregate marker; the two can never disagree because they are
        // read off the same field.
        aggregated: row.cellId === null,
        cellKey: row.cellId,
        coverageFraction: row.coverageFraction,
      } satisfies ClimateFieldFeatureProperties,
    };
  });
}

/**
 * Convert a fail-closed Parquet day result into the existing climate GeoJSON contract, declaring
 * the rung it was read from.
 *
 * `latticeCellCount` is the DETAIL lattice's count and is published only for the detail rung: it
 * is the denominator "267 of the 397 cells in view" is measured against, and a coarse rung's cells
 * are not drawn from that lattice at all. Zero there means "not measured on this request", exactly
 * as `emptyCollection`'s default does.
 */
export function parquetClimateFieldCollection(
  result: Exclude<ClimateResult, { state: "upstream_unavailable" }>,
  signal: ClimateFieldSignalId,
  variant: AirTemperatureVariant,
  bbox: string,
  zoomTier: ZoomTier,
  requestedRenderForm?: ClimateRenderForm
): ZoomedClimateFieldCollection {
  const renderForm = tierRenderForm(zoomTier, signal, requestedRenderForm);
  const latticeCellCount =
    zoomTier === DETAIL_ZOOM_TIER ? climateFieldLatticeCellCount(bbox) : 0;
  if (result.state !== "ready" || result.data.length === 0) {
    return emptyCollection(
      signal,
      variant,
      renderForm,
      result.requestedDay,
      latticeCellCount,
      zoomTier
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
      latticeCellCount,
      zoomTier
    );
  }
  return {
    type: "FeatureCollection",
    features,
    availability: "published",
    reason: null,
    granularity: granularityForZoomTier(zoomTier),
    zoomTier,
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
