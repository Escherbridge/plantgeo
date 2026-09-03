/**
 * The frozen product-to-render-class registry: which spatial FORM each layer may be drawn in
 * at each zoom band, and the envelope a reader must return so the client never infers support
 * from a null cell id or a layer name.
 *
 * Rationale, the 2026-09-01 production finding it answers and the fire-perimeter tripwire are
 * in src/lib/map/AGENTS.md "The layer render contract". The vocabulary is frozen from
 * conductor/tracks/multiscale_polygon_surface_20260901/spec.md and is CLOSED: a renderer that
 * needs a form not listed here changes this file and its test, never its own call site.
 */

import {
  CLIMATE_FIELD_TOGGLE_IDS,
  type ClimateFieldToggleId,
} from "@/lib/environmental/climate-field";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import {
  latticeCellSpan,
  resolveZoomTier,
  type ServedCellLattice,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";

/** The four product classes the spec's render table enumerates. */
export const RENDER_CLASSES = [
  "continuous_field",
  "event_point",
  "native_polygon",
  "reference_or_unavailable",
] as const;

/** Which of the four render classes a product belongs to. */
export type RenderClass = (typeof RENDER_CLASSES)[number];

/**
 * Every spatial form a served feature may claim. Closed on purpose: this union is the whole
 * of what a `supportKind` on the wire may say, so a reader cannot invent a ninth form and a
 * client cannot be handed one it has no rule for.
 */
export const SUPPORT_KINDS = [
  "raw_point",
  "aggregate_cell",
  "heatmap",
  "cluster",
  "tessellated_cell",
  "isoband",
  "raster_surface",
  "native_polygon",
  "unavailable",
] as const;

/** One member of the closed form vocabulary above. */
export type SupportKind = (typeof SUPPORT_KINDS)[number];

/**
 * The forms that stand for MORE THAN ONE contributor. The distinction the acceptance gate
 * "event aggregates are visually and textually distinct from native perimeters" turns on:
 * an aggregate may never be captioned, hovered or legended as an individual observation.
 */
export const AGGREGATE_SUPPORT_KINDS = [
  "aggregate_cell",
  "heatmap",
  "cluster",
  "tessellated_cell",
  "isoband",
  "raster_surface",
] as const;

/** One of the multi-contributor forms above. */
export type AggregateSupportKind = (typeof AGGREGATE_SUPPORT_KINDS)[number];

/**
 * Widened to `SupportKind` deliberately: the annotation makes the compiler prove that every
 * aggregate form is also a member of the closed vocabulary, so the two lists cannot drift.
 */
const AGGREGATE_SUPPORT_KIND_SET = new Set<SupportKind>(AGGREGATE_SUPPORT_KINDS);

/** True when the form stands for several contributors rather than one source feature. */
export function isAggregateSupportKind(
  supportKind: SupportKind
): supportKind is AggregateSupportKind {
  return AGGREGATE_SUPPORT_KIND_SET.has(supportKind);
}

/**
 * The three bands a reader argues about, over the four physical rungs the warehouse writes.
 *
 * Named as adjectives where `ZoomTier` deliberately is not: a tier is a PARTITION PATH and
 * must stay true if the ladder grows a rung, whereas a band is a rendering decision -- "what
 * may this look like here" -- and the spec's table has exactly three columns of those.
 */
export const ZOOM_BANDS = ["coarse", "middle", "detail"] as const;

/** One of the three rendering bands. */
export type ZoomBand = (typeof ZOOM_BANDS)[number];

/**
 * Which band each physical rung renders in. Total over `ZoomTier` and single-valued, so one
 * zoom request selects exactly one rung which selects exactly one band -- the spec's first
 * acceptance gate, "one physical rung renders at a time".
 *
 * z0 and z5 share the coarse band because the serving side already treats them as one:
 * `granularityForZoomTier` (`server/services/zoom-granularity.ts`) reports `granularity` as
 * "detail" at z13, "regional-average" at z9 and "coarse-average" for BOTH lower rungs.
 * Splitting them here would give the client a fourth vocabulary the server never speaks.
 */
export const ZOOM_TIER_BANDS: Readonly<Record<ZoomTier, ZoomBand>> = {
  0: "coarse",
  5: "coarse",
  9: "middle",
  13: "detail",
};

/** The band a published rung renders in. */
export function zoomBandForTier(zoomTier: ZoomTier): ZoomBand {
  return ZOOM_TIER_BANDS[zoomTier];
}

/**
 * The band a live map zoom renders in, resolved through the one ladder. Throws
 * `ZoomTierResolutionError` for a zoom no published rung serves rather than guessing a band.
 */
export function resolveZoomBand(mapZoom: number): ZoomBand {
  return zoomBandForTier(resolveZoomTier(mapZoom));
}

/** How the values inside one envelope were combined. `none` means the source form, unchanged. */
export const AGGREGATION_METHODS = ["none", "count", "sum", "mean", "dissolve", "simplify"] as const;

/** One aggregation method from the closed list above. */
export type AggregationMethod = (typeof AGGREGATION_METHODS)[number];

/** Where an envelope's numbers came from, carried with them so a drawn cell can be attributed. */
export interface SupportProvenance {
  /** The Parquet lane or `geo.layers` name the rows were read from. */
  sourceLayer: string;
  /** The day the values describe, `YYYY-MM-DD`. */
  observedDay: string;
  /** Newest source observation folded in, ISO 8601; null when the lane publishes no timestamp. */
  newestObservedAt: string | null;
  /** The attribution that must be shown wherever these values are drawn. */
  attribution: string;
}

/**
 * What every aggregate envelope declares about its own support. The shape a reader returns so
 * the client never has to infer support from a null cell id or a layer name.
 *
 * `supportId` is STABLE and never null. Before this contract, the soil-field reader encoded
 * "this is an aggregate" as `cell_id === null` at every rung below z13
 * (`decodeSoilFieldRows`) and the client re-derived cell width from a private tier table, so a
 * rung whose rows happened to carry ids was indistinguishable from raw observations. A producer
 * whose rows have no natural id mints one from the cell origin here rather than sending null.
 *
 * `origin` is the difference between a cell drawn from its corner and one drawn around its
 * centre -- `soilFieldPolygon` already branches on exactly that, offsetting by half a cell for
 * the base lattice and not at all for the aggregated rungs. Getting it wrong shifts a whole
 * field by half a cell, which reads as a registration error rather than a bug.
 */
export interface AggregateEnvelopeSupport {
  /** The published rung these features were read from. */
  zoomTier: ZoomTier;
  /** The form the features are drawn in; must be permitted for the layer at this rung's band. */
  supportKind: SupportKind;
  /** Stable identity for this support unit -- canonical cell id, or the source feature id. */
  supportId: string;
  /** Whether the coordinates locate the cell's corner or its centre. */
  origin: "cell_origin" | "cell_center";
  /** Cell width in degrees; omitted only for native source geometry, which has no cell size. */
  cellWidthDegrees?: number;
  /** Cell height in degrees; omitted only for native source geometry. */
  cellHeightDegrees?: number;
  /**
   * The cell's SNAPPED SOUTH-WEST CORNER, `[longitude, latitude]`, exactly as the serving side
   * placed it on the lattice it read the row from.
   *
   * The one field that makes the client's square and the server's square the same square. The
   * serving lattice has a PHASE as well as a pitch (`ServedCellLattice.originOffsetDegrees`:
   * zero for the ladder's own grids, minus half a cell for the quarter-degree and one-degree
   * base lattices), and until 2026-09-02 the phase was not on the wire at all -- so
   * `supportCellPolygon` assumed a lattice anchored at whole multiples of the cell size and
   * disagreed with `servedCellLattice` by up to half a cell wherever that assumption was wrong.
   * Vegetation at z9 and z5 is exactly that case: a 0.25-degree base grain kept across the
   * ladder's 0.01 and 0.2 grids, on a half-offset phase.
   *
   * Sent as the corner rather than as the whole footprint because the corner is all the far
   * edge needs: it carries the phase (`corner - round(corner / size) * size`) and the index
   * with it, so the client re-derives `latticeCellSpan`'s own `offset + (index + 1) * size` and
   * lands on the SAME double as the neighbouring cell's west edge. Computing the far edge as
   * `corner + size` instead disagrees with the neighbour in the last bit for about 30% of cells
   * on the z13/z9/z5 grids -- measured, not feared -- which is precisely the hairline seam the
   * spec's "neighboring cells share bit-identical boundaries" gate forbids.
   *
   * Absent on an envelope that describes no cell (`raw_point`, native geometry) and on the
   * COLLECTION-level envelopes (`soilFieldSupport`, climate's `collectionSupport`), which
   * describe a lane's whole lattice rather than one cell and whose features are already built
   * server-side by `tessellatedCellPolygon`.
   */
  cellOriginDegrees?: readonly [number, number];
  /** How the contributors were combined. */
  aggregationMethod: AggregationMethod;
  /** How many source observations or features this envelope stands for. Never inferred. */
  contributorCount: number;
  provenance: SupportProvenance;
}

/**
 * How far a declared corner may sit off the lattice its own cell size defines before the
 * offset is taken as real rather than as floating-point noise. 1e-9 degrees is about a tenth
 * of a millimetre on the ground: below it nothing but IEEE-754 rounding can be responsible.
 */
const LATTICE_SNAP_TOLERANCE_DEGREES = 1e-9;

/**
 * The footprint an envelope declares, as a closed GeoJSON ring, or null when it declares none.
 *
 * The client's ONE derivation of a cell's extent, and it derives it from the envelope's own
 * numbers rather than from a private per-layer tier table -- the guess `AggregateEnvelopeSupport`
 * exists to replace. An envelope carrying no `cellWidthDegrees`/`cellHeightDegrees` (native
 * geometry, a raw point, or a reader that has not filled them in) yields null, and the caller
 * draws its marker form rather than inventing a square.
 *
 * **Neighbouring cells share bit-identical edges.** Both edges of every cell are one
 * `latticeCellSpan` call, so cell i's east edge and cell i+1's west edge are the SAME expression
 * over the same operands and are equal to the bit. Computing east as `west + size` instead leaves
 * sub-ULP disagreements between neighbours -- for about 30% of cells on the 0.005, 0.01 and 0.2
 * grids, measured -- which is invisible on screen but is not what the spec's "neighboring cells
 * share bit-identical boundaries" gate asks for.
 *
 * **ONE function computes cell edges, and it lives in `zoom-tiers.ts`.** Every edge below is a
 * `latticeCellSpan` result -- the same expression the serving side's `tessellatedCellPolygon`
 * evaluates -- so there is no second piece of arithmetic that can drift from it. What differs
 * between the two callers is only where the lattice comes from: the serving side knows the LANE
 * and reads it from `LANE_BASE_LATTICES`, while a renderer holds features rather than a lane id
 * and reads it off the envelope. Re-deriving the footprint from a per-layer table on this side is
 * precisely the inference `AggregateEnvelopeSupport` exists to end.
 *
 * `cellOriginDegrees` is what closes the gap between them: the serving side states the corner it
 * actually snapped to, and this builder takes it verbatim. Where an envelope carries none -- a
 * payload replayed from before 2026-09-02 -- the corner is derived from the anchor and the phase
 * is read back out of the corner itself, which reproduces the serving lattice exactly for every
 * grid this platform publishes and keeps a producer's deliberate off-lattice phase where it put it
 * rather than snapping the cell half a width away.
 */
export function supportCellPolygon(
  anchorLongitude: number,
  anchorLatitude: number,
  support: AggregateEnvelopeSupport
): GeoJSON.Polygon | null {
  const cellWidth = support.cellWidthDegrees;
  const cellHeight = support.cellHeightDegrees;
  if (cellWidth === undefined || cellHeight === undefined) return null;
  if (!Number.isFinite(cellWidth) || cellWidth <= 0) return null;
  if (!Number.isFinite(cellHeight) || cellHeight <= 0) return null;

  const declared = support.cellOriginDegrees;
  // `cell_center` locates the middle of the square, `cell_origin` its south-west corner --
  // the same half-cell branch the serving side's lattice makes. Skipped entirely when the
  // envelope declared the corner, because a declared corner is already the south-west one.
  const cornerLongitude =
    declared?.[0] ??
    (support.origin === "cell_center" ? anchorLongitude - cellWidth / 2 : anchorLongitude);
  const cornerLatitude =
    declared?.[1] ??
    (support.origin === "cell_center" ? anchorLatitude - cellHeight / 2 : anchorLatitude);
  if (!Number.isFinite(cornerLongitude) || !Number.isFinite(cornerLatitude)) return null;

  const [west, east] = declaredCellSpan(cornerLongitude, cellWidth);
  const [south, north] = declaredCellSpan(cornerLatitude, cellHeight);

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
 * The two edges of the cell whose south-west corner is `corner`, through the one span builder.
 *
 * The lattice is read back OUT of the corner: its index is `round(corner / cellSize)` and its
 * phase is whatever that index leaves over. A corner already on a lattice anchored at whole
 * multiples of the cell size leaves a residue no larger than IEEE-754 noise and is given a phase
 * of exactly zero, so neighbouring cells share the identical `offset + index * size` expression
 * and therefore the identical double. A corner that leaves a real residue was binned by its
 * producer on some other phase, and that phase is carried rather than snapped away -- snapping
 * would move the cell by up to half its width, which reads as a registration error rather than
 * as a bug.
 */
function declaredCellSpan(corner: number, cellSize: number): readonly [number, number] {
  const latticeIndex = Math.round(corner / cellSize);
  const residue = corner - latticeIndex * cellSize;
  const lattice: ServedCellLattice = {
    cellSizeDegrees: cellSize,
    originOffsetDegrees: Math.abs(residue) <= LATTICE_SNAP_TOLERANCE_DEGREES ? 0 : residue,
    // Both zero: a corner is not a served coordinate that a derivation floored, so there is no
    // half-step to add back, and the index above is already the cell's own.
    snapCorrectionDegrees: 0,
    origin: "cell_origin",
  };
  return latticeCellSpan(latticeIndex, lattice);
}

/**
 * A form the SHIPPED renderer draws that this contract does not permit, recorded rather than
 * legalised.
 *
 * The contract is a statement about what a layer MAY be drawn as. Widening a band to match a
 * renderer that is currently wrong would make the contract describe the code instead of governing
 * it, and the discrepancy -- the only thing that makes the gap findable -- would disappear. So a
 * known deviation is carried here instead: named form, named owner, dated, with the reason.
 *
 * Every field is required, and the test enforces that each deviation names an owner and a date:
 * an undated one with no owner is indistinguishable from a permanent second contract.
 */
export interface ShippedRenderDeviation {
  /** The form actually drawn today, which `permittedForms` deliberately still excludes. */
  form: SupportKind;
  /** The track slice accountable for closing it. */
  owner: string;
  /** When the deviation was recorded, `YYYY-MM-DD`. */
  recordedOn: string;
  note: string;
}

/** One layer's frozen rendering permissions. */
export interface LayerRenderContractEntry {
  /** The registry toggle this contract governs. */
  layerId: LayerToggleId;
  renderClass: RenderClass;
  /** The only forms this layer may be drawn in, per band. Never empty. */
  permittedForms: Readonly<Record<ZoomBand, readonly SupportKind[]>>;
  /**
   * The product's real support in degrees, or null when the layer has no single fixed one.
   * Set where a renderer would otherwise be free to draw a footprint finer than the ground
   * that was actually measured -- an envelope for such a layer may never declare a
   * `cellWidthDegrees` or `cellHeightDegrees` below this.
   */
  declaredSupportDegrees: number | null;
  /** A recorded gap between this contract and the shipped renderer; absent when there is none. */
  shippedDeviation?: ShippedRenderDeviation;
}

const CONTINUOUS_FIELD_AGGREGATE_FORMS: readonly SupportKind[] = [
  "tessellated_cell",
  "isoband",
  "raster_surface",
];
const CONTINUOUS_FIELD_DETAIL_FORMS: readonly SupportKind[] = [
  "tessellated_cell",
  "raster_surface",
];
const EVENT_POINT_AGGREGATE_FORMS: readonly SupportKind[] = [
  "aggregate_cell",
  "heatmap",
  "cluster",
];
const EVENT_POINT_DETAIL_FORMS: readonly SupportKind[] = ["raw_point"];
/**
 * FIRMS publishes no raw rung: its z13 rows are cells (`cell_longitude`/`cell_latitude`/
 * `detection_count`), so the truthful detail form is the cell, and `raw_point` is withheld
 * because permitting it would license a renderer to claim individual detections the lane cannot
 * serve. Consistent with the spec's own carve-out -- "Genuine stations remain points at detail
 * zoom" names the layers fire is not one of.
 */
const FIRE_DETECTION_DETAIL_FORMS: readonly SupportKind[] = ["aggregate_cell"];
const NATIVE_POLYGON_FORMS: readonly SupportKind[] = ["native_polygon"];
const UNAVAILABLE_FORMS: readonly SupportKind[] = ["unavailable"];
const FIXED_SUPPORT_CELL_FORMS: readonly SupportKind[] = ["tessellated_cell"];

/** Climate, soil-state and other modelled fields: complete tessellation, isobands or a surface. */
function continuousFieldEntry(layerId: LayerToggleId): LayerRenderContractEntry {
  return {
    layerId,
    renderClass: "continuous_field",
    permittedForms: {
      coarse: CONTINUOUS_FIELD_AGGREGATE_FORMS,
      middle: CONTINUOUS_FIELD_AGGREGATE_FORMS,
      detail: CONTINUOUS_FIELD_DETAIL_FORMS,
    },
    declaredSupportDegrees: null,
  };
}

/**
 * A continuous field whose support is a fixed lattice, drawn as discrete cells at every band.
 * Isobands and raster surfaces are withheld: both assert the field varies smoothly BETWEEN
 * the samples, which is the fictitious finer footprint the spec forbids for this case.
 */
function fixedSupportFieldEntry(
  layerId: LayerToggleId,
  declaredSupportDegrees: number
): LayerRenderContractEntry {
  return {
    layerId,
    renderClass: "continuous_field",
    permittedForms: {
      coarse: FIXED_SUPPORT_CELL_FORMS,
      middle: FIXED_SUPPORT_CELL_FORMS,
      detail: FIXED_SUPPORT_CELL_FORMS,
    },
    declaredSupportDegrees,
  };
}

/** Detections, gauges, stations and sensors: declared aggregates above, raw points at detail. */
function eventPointEntry(
  layerId: LayerToggleId,
  detailForms: readonly SupportKind[] = EVENT_POINT_DETAIL_FORMS
): LayerRenderContractEntry {
  return {
    layerId,
    renderClass: "event_point",
    permittedForms: {
      coarse: EVENT_POINT_AGGREGATE_FORMS,
      middle: EVENT_POINT_AGGREGATE_FORMS,
      detail: detailForms,
    },
    declaredSupportDegrees: null,
  };
}

/** Products with real source geometry: generalize or dissolve, never re-derive. */
function nativePolygonEntry(layerId: LayerToggleId): LayerRenderContractEntry {
  return {
    layerId,
    renderClass: "native_polygon",
    permittedForms: {
      coarse: NATIVE_POLYGON_FORMS,
      middle: NATIVE_POLYGON_FORMS,
      detail: NATIVE_POLYGON_FORMS,
    },
    declaredSupportDegrees: null,
  };
}

/** Products with no valid spatial form yet: say so explicitly rather than drawing a guess. */
function referenceOrUnavailableEntry(layerId: LayerToggleId): LayerRenderContractEntry {
  return {
    layerId,
    renderClass: "reference_or_unavailable",
    permittedForms: {
      coarse: UNAVAILABLE_FORMS,
      middle: UNAVAILABLE_FORMS,
      detail: UNAVAILABLE_FORMS,
    },
    declaredSupportDegrees: null,
  };
}

/**
 * The nine NASA POWER signals, folded from the signal vocabulary rather than hand-listed --
 * the same derivation `LAYER_REGISTRY` uses, so a tenth signal arrives with a contract instead
 * of failing an exhaustiveness check nobody expected to touch.
 */
const CLIMATE_FIELD_CONTRACT_ENTRIES = CLIMATE_FIELD_TOGGLE_IDS.reduce(
  (entries, toggleId) => {
    entries[toggleId] = continuousFieldEntry(toggleId);
    return entries;
    // Seeded with a cast because the record is only exhaustive once the fold has run; the
    // fold is over `CLIMATE_FIELD_TOGGLE_IDS`, which is derived from a table exhaustive over
    // the signal union, so every key does get written.
  },
  {} as Record<ClimateFieldToggleId, LayerRenderContractEntry>
);

/**
 * One entry per registry layer. Typed as a total record over `LayerToggleId`, so a new toggle
 * fails to compile here rather than reaching the map with no declared form.
 */
export const LAYER_RENDER_CONTRACT: Readonly<Record<LayerToggleId, LayerRenderContractEntry>> = {
  ...CLIMATE_FIELD_CONTRACT_ENTRIES,

  // Event points. FIRMS detections, USGS gauges, Open-Meteo stations and published sensor
  // rows: real observations at real coordinates, whose coarse forms count contributors and
  // never trace an extent.
  // Fire's detail band is cells, not raw points: the lane has no raw rung. See
  // `FIRE_DETECTION_DETAIL_FORMS`.
  fire: eventPointEntry("fire", FIRE_DETECTION_DETAIL_FORMS),
  water: eventPointEntry("water"),
  weather: eventPointEntry("weather"),
  sensors: eventPointEntry("sensors"),

  // Native polygons. Every one of these has published source geometry; the only permitted
  // zoom treatment is topology-preserving generalization or dissolve.
  "fire-perimeters": nativePolygonEntry("fire-perimeters"),
  "burn-severity": nativePolygonEntry("burn-severity"),
  drought: nativePolygonEntry("drought"),
  "evacuation-zones": nativePolygonEntry("evacuation-zones"),
  watersheds: nativePolygonEntry("watersheds"),

  // The one native-polygon product that does NOT draw its own geometry at the default camera.
  // `readSummaryFeatures` (`server/services/usda-soil.ts`) answers one counted Point per lattice
  // cell once the viewport exceeds what the polygon-union budget covers, and
  // `soilSurveySummaryLayer` (`map/layers.ts`) paints those as count-scaled circles -- an
  // aggregate point summary, drawn under a contract that permits `native_polygon` and nothing
  // else. Recorded rather than legalised: widening the coarse band would make this the only
  // native-polygon layer allowed to stop drawing its producer's geometry, and would erase the
  // record that the two disagree.
  "soil-survey": {
    ...nativePolygonEntry("soil-survey"),
    shippedDeviation: {
      form: "aggregate_cell",
      owner: "multiscale_polygon_surface_20260901 m2",
      recordedOn: "2026-09-02",
      note:
        "readSummaryFeatures returns counted Points and soilSurveySummaryLayer draws them as " +
        "count-scaled circles at the default PNW camera: a ~98 sq deg viewport cannot be " +
        "unioned honestly against the 0.48 sq deg budget, so the summary is a real answer to a " +
        "real constraint rather than a rendering slip. Closing it is an owner decision between " +
        "declaring a tessellated cell for the summary rung and re-classing the layer, not a " +
        "renderer fix.",
    },
  },

  // Continuous fields. The three ERA5-Land lanes, aggregated onto a coarser lattice by the
  // reader at every rung below z13 and drawn as cells, isobands or a surface.
  "soil-moisture": continuousFieldEntry("soil-moisture"),
  "soil-temperature": continuousFieldEntry("soil-temperature"),
  "soil-vpd": continuousFieldEntry("soil-vpd"),

  // The one field pinned to its measured support: 0.25 degrees, the cells this platform
  // actually observed. Never drawn finer, and never smoothed into a surface across cells the
  // lane did not fill.
  //
  // Carried a `shippedDeviation` from 2026-09-02 until slice m3 closed it the same day:
  // `presentParquetVegetation` emitted a Point at each cell's centre and `VegetationLayer`
  // painted it as a zoom-scaled circle, which is `raw_point` on this vocabulary and exactly the
  // claim `declaredSupportDegrees` exists to forbid. Both now draw the declared 0.25-degree
  // tessellated cell, so the contract and the renderer agree and there is nothing to record.
  vegetation: fixedSupportFieldEntry("vegetation", 0.25),

  // Reference or unavailable. `soil` has no published raster release at all; the three
  // community surfaces have no declared spatial support in this contract yet.
  soil: referenceOrUnavailableEntry("soil"),
  "demand-heatmap": referenceOrUnavailableEntry("demand-heatmap"),
  interventions: referenceOrUnavailableEntry("interventions"),
  "strategy-recommendations": referenceOrUnavailableEntry("strategy-recommendations"),
};

/** Every contract entry, in declaration order. */
export function layerRenderContractEntries(): LayerRenderContractEntry[] {
  return (Object.keys(LAYER_RENDER_CONTRACT) as LayerToggleId[]).map(
    (layerId) => LAYER_RENDER_CONTRACT[layerId]
  );
}

/** The render class a layer belongs to. */
export function renderClassOf(layerId: LayerToggleId): RenderClass {
  return LAYER_RENDER_CONTRACT[layerId].renderClass;
}

/**
 * The forms this layer may be drawn in at this map zoom. Never empty. Throws
 * `ZoomTierResolutionError` for a zoom no published rung serves.
 */
export function permittedFormsFor(layerId: LayerToggleId, zoom: number): readonly SupportKind[] {
  return LAYER_RENDER_CONTRACT[layerId].permittedForms[resolveZoomBand(zoom)];
}

/**
 * The same table keyed by the PUBLISHED RUNG rather than by a live map zoom.
 *
 * Presentation code never holds a zoom: it holds features whose envelopes declare the rung they
 * were read at (`AggregateEnvelopeSupport.zoomTier`), and a retained frame outlives the zoom it
 * was fetched for. Resolving that frame's forms through the current zoom would ask the contract
 * about a band the cells in hand were never aggregated for.
 */
export function permittedFormsForTier(
  layerId: LayerToggleId,
  zoomTier: ZoomTier
): readonly SupportKind[] {
  return LAYER_RENDER_CONTRACT[layerId].permittedForms[zoomBandForTier(zoomTier)];
}

/** True when the layer may be drawn in this form at this zoom. */
export function isFormPermitted(
  layerId: LayerToggleId,
  zoom: number,
  supportKind: SupportKind
): boolean {
  return permittedFormsFor(layerId, zoom).includes(supportKind);
}

/** True when the layer may be drawn in this form at this published rung. */
export function isFormPermittedForTier(
  layerId: LayerToggleId,
  zoomTier: ZoomTier,
  supportKind: SupportKind
): boolean {
  return permittedFormsForTier(layerId, zoomTier).includes(supportKind);
}

/**
 * Raised when an event-point layer is asked to draw itself as native polygon geometry --
 * the one confusion this track exists to make impossible.
 */
export class PerimeterMisrepresentationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PerimeterMisrepresentationError";
  }
}

/**
 * Fail loudly when an `event_point` layer is handed `native_polygon`.
 *
 * Satellite detection-density cells are NOT physical fire perimeters: a cell says "n hotspots
 * were detected in this square", and rendering it as native geometry publishes an authoritative
 * burned extent nobody measured. `fire-perimeters` and `burn-severity` are the products that
 * legitimately carry that claim, and they are separate layers with their own source geometry.
 *
 * A throw rather than a silent downgrade on purpose: a wrong form here is a truth claim, and a
 * quietly corrected one would ship the calling bug to the next renderer.
 */
export function assertNotPerimeter(layerId: LayerToggleId, supportKind: SupportKind): void {
  if (supportKind !== "native_polygon") return;
  if (renderClassOf(layerId) !== "event_point") return;
  throw new PerimeterMisrepresentationError(
    `${layerId} is an event_point layer: its aggregates are detection/observation density cells, ` +
      `never native_polygon perimeters`
  );
}
