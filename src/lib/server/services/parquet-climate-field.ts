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
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import {
  BASE_ZOOM_TIER,
  LANE_BASE_LATTICES,
  latticeCellIndexContaining,
  servedCellLattice,
  tessellatedCellPolygon,
  type ServedCellLattice,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import { granularityForZoomTier } from "@/lib/server/services/zoom-granularity";
import type {
  ParquetClimateFieldObservation,
  ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";

/**
 * The one-degree NASA POWER sampling lattice, and the rung-by-rung cell it becomes.
 *
 * WAVE 1 DREW POINTS BELOW z13 and a half-degree square at z13, and both were wrong in the same
 * way: the module invented its own pitch instead of reading one. The coarse rungs got no footprint
 * at all -- "honest, self-locating, and needing no pitch" was the note -- so a zoomed-out field
 * was a scatter of dots over blank ground, and the detail rung drew a half-degree cell on a
 * one-degree lattice, leaving three quarters of the viewport as background between cells. That
 * pair IS the "separated rectangular climate blocks" the 2026-09-01 production assessment recorded.
 *
 * Both are now one lookup into the shared tier table (`servedCellLattice`, zoom-tiers.ts), which
 * takes the coarser of the ladder's grid and this lane's own base grain and rebuilds every corner
 * from a lattice index, so adjacent cells share the same double rather than a nearly-equal one.
 */
const CLIMATE_FIELD_LANE = LANE_BASE_LATTICES["climate-field"];

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

/**
 * How many cells of the SERVED rung's lattice this viewport could be filled with.
 *
 * The denominator the panel's "N of M cells in view" sentence is measured against, and it now has
 * a value at every rung instead of only the detail one. Wave 1 published 0 below z13 -- correctly,
 * because the number it had was the detail lattice's own count and a coarse rung is not drawn from
 * it -- but the fix for a denominator measured on the wrong lattice is to measure it on the right
 * one, not to withhold it. Frozen lattice centres are folded onto the served rung's cells and the
 * DISTINCT cells are counted, so at z0 a whole viewport of samples collapses into the handful of
 * five-degree cells that actually get drawn.
 *
 * Counted by CENTRE, never by footprint: a cell whose centre is outside the bbox is not in view
 * for this purpose, which is the rule that keeps the numerator and denominator on the same test.
 *
 * `latticeCellIndexContaining`, not `latticeCellIndex`: a frozen lattice centre means only itself
 * and was never floored onto a rung's grid, so the served-coordinate recovery would push it into
 * the next cell up whenever it sits past that cell's midpoint.
 */
export function climateFieldLatticeCellCount(bbox: string, zoomTier: ZoomTier): number {
  const [west, south, east, north] = parseBbox(bbox);
  const lattice = servedCellLattice(zoomTier, CLIMATE_FIELD_LANE);
  const servedCells = new Set<string>();
  for (const [latitude, minimumLongitude, maximumLongitude, excluded = []] of
    CLIMATE_FIELD_LATTICE_ROWS) {
    const excludedLongitudes: readonly number[] = excluded;
    if (latitude < south || latitude > north) {
      continue;
    }
    for (let longitude = minimumLongitude; longitude <= maximumLongitude; longitude += 1) {
      if (excludedLongitudes.includes(longitude)) continue;
      if (longitude >= west && longitude <= east) {
        servedCells.add(
          `${latticeCellIndexContaining(longitude, lattice)}:` +
            `${latticeCellIndexContaining(latitude, lattice)}`
        );
      }
    }
  }
  return servedCells.size;
}

/**
 * A collection that also declares WHICH rung answered it and what its features stand for.
 *
 * Declared here rather than added to `PublishedClimateFieldCollection`: that interface is the
 * PostgreSQL read model's own and is shared with a reader that has no zoom ladder. Extending it
 * locally keeps the tier on every Parquet answer without asserting the older reader publishes one.
 */
export interface ZoomedClimateFieldCollection extends PublishedClimateFieldCollection {
  /** The one physical rung the rows came from; exactly one per request. */
  zoomTier: ZoomTier;
  /**
   * ONE envelope for the whole collection. Every feature here shares the rung, the cell size, the
   * origin semantics and the attribution; the part that varies per feature -- the cell's own
   * identity -- is already on each feature as `cellKey`.
   */
  support: AggregateEnvelopeSupport;
}

/**
 * The form this rung may actually be drawn in.
 *
 * The wave-1 rule was "everything below z13 degrades to points", which cost the coarse rungs any
 * footprint at all. The rule now comes from `LAYER_RENDER_CONTRACT`, which permits a continuous
 * field `tessellated_cell` at every band and `isoband` at the coarse and middle bands ONLY: an
 * isoband asserts the field varies smoothly BETWEEN the samples, and at the rung that serves the
 * lane's own cells the honest form is those cells rather than an interpolation across them.
 *
 * So the single degrade left is a contoured signal at the detail rung, which is served filled
 * instead, and it is reported back in `renderForm` so a client cannot mistake the returned
 * geometry for the one it asked for.
 */
function tierRenderForm(
  zoomTier: ZoomTier,
  signal: ClimateFieldSignalId,
  requested: ClimateRenderForm | undefined
): ClimateRenderForm {
  const resolved = resolveClimateRenderForm(signal, requested);
  return resolved === "isoline" && zoomTier === BASE_ZOOM_TIER ? "field" : resolved;
}

/** The support every feature of one answer declares, folded from the rows that drew them. */
function collectionSupport(
  signal: ClimateFieldSignalId,
  zoomTier: ZoomTier,
  renderForm: ClimateRenderForm,
  observedDay: string,
  rows: readonly ParquetClimateFieldObservation[]
): AggregateEnvelopeSupport {
  const lattice = servedCellLattice(zoomTier, CLIMATE_FIELD_LANE);
  const dissolved = renderForm === "isoline";
  const newestObservedAt = rows.reduce<string | null>(
    (newest, row) =>
      newest === null || Date.parse(row.newestObservedAt) > Date.parse(newest)
        ? row.newestObservedAt
        : newest,
    null
  );
  return {
    zoomTier,
    // `isoband` for the dissolved form and `tessellated_cell` otherwise. The envelope says what
    // ground a value stands for; `renderForm` says what mark is drawn on it -- and since
    // 2026-09-02 the only marks left are the cell itself and the band dissolved across cells.
    supportKind: dissolved ? "isoband" : "tessellated_cell",
    supportId: `climate-field-${signal}:${observedDay}:z${zoomTier}`,
    origin: lattice.origin,
    cellWidthDegrees: lattice.cellSizeDegrees,
    cellHeightDegrees: lattice.cellSizeDegrees,
    aggregationMethod: dissolved ? "dissolve" : "mean",
    contributorCount: rows.reduce((total, row) => total + row.observationCount, 0),
    provenance: {
      sourceLayer: rows[0]?.support.provenance.sourceLayer ?? `climate-field-${signal}`,
      observedDay,
      newestObservedAt,
      attribution: CLIMATE_FIELD_ATTRIBUTION,
    },
  };
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
    support: collectionSupport(signal, zoomTier, renderForm, requestedDay, []),
  };
}

function cellFeatures(
  signal: ClimateFieldSignalId,
  renderForm: ClimateRenderForm,
  lattice: ServedCellLattice,
  rows: readonly ParquetClimateFieldObservation[]
): GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon | GeoJSON.Point>[] {
  const definition = climateFieldSignalDefinition(signal);
  if (renderForm === "isoline") {
    // Dissolved over the SERVED rung's lattice, never the detail one. Passing the wrong step makes
    // `buildIsobands` read a regular lattice as a scatter -- every square fails its corner test and
    // the band comes back empty or in pieces, which on screen is a seam wherever one batch of rows
    // met the next.
    const bands = buildIsobands(
      rows.map((row) => ({ lon: row.longitude, lat: row.latitude, value: row.value })),
      lattice.cellSizeDegrees,
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
      // The row's own support id, which is the stored cell identity where the rung has one and a
      // minted rung-and-position id where it does not. Never null, so a feature cannot re-enter the
      // map as a new one on every pan and defeat MapLibre's own diffing.
      id: row.support.supportId,
      // Always the cell, never a dot at its centre: `tierRenderForm` resolves through
      // `resolveClimateRenderForm`, which offers no signal a point form and maps a stored
      // `symbol` onto `field`, so `symbol` cannot reach here. The branch that drew a Point for it
      // was deleted on 2026-09-02 with the offered form.
      geometry: tessellatedCellPolygon(row.longitude, row.latitude, lattice),
      properties: {
        value: row.value,
        unit: definition.unit,
        bandIndex: band.bandIndex,
        bandLabel: band.label,
        observedDay: row.observedDay,
        // The declared rung, not the cell identity's nullability. The two agreed while only the
        // detail rung carried ids; `support` says it outright and cannot be wrong about its own
        // rung, which is the whole point of the envelope.
        aggregated: row.support.zoomTier !== BASE_ZOOM_TIER,
        cellKey: row.cellId,
        coverageFraction: row.coverageFraction,
      } satisfies ClimateFieldFeatureProperties,
    };
  });
}

/**
 * Convert a fail-closed Parquet day result into the existing climate GeoJSON contract, declaring
 * the rung it was read from and the support its features stand for.
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
  const latticeCellCount = climateFieldLatticeCellCount(bbox, zoomTier);
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
  const lattice = servedCellLattice(zoomTier, CLIMATE_FIELD_LANE);
  const rows = result.data.slice(0, CLIMATE_FIELD_MAX_CELLS);
  const features = cellFeatures(signal, renderForm, lattice, rows);
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
    support: collectionSupport(signal, zoomTier, renderForm, result.servedDay, rows),
  };
}
