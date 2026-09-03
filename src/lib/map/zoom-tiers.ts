/**
 * The uniform zoom ladder every layer's Parquet partitions are written under, and which tier
 * serves a given map zoom.
 *
 * Mirrors `services/agri-data-service/src/agri_data_service/foundation/parquet/zoom.py`
 * exactly: same four breakpoints, same "serve from the tier at or below the request" rule, and
 * the same two-digit zero-padded path segment (`zoom=09`, not `zoom=9` -- `zoom.py`'s
 * `ZOOM_SEGMENT_DIGITS` and its `paths.py` regex both require exactly two digits). The two
 * implementations must never diverge: a client that resolves a different tier than the writer
 * published requests a partition path that was never written and silently reads nothing where
 * data exists.
 *
 * ONE ladder for every layer, deliberately -- see `zoom.py`'s header for the per-layer-ladder
 * cost this replaces (watersheds at 4/6/8/10/12, strategy recommendations at 6/11, neither
 * nameable without knowing the layer's private table). Tiers are named by their minimum zoom,
 * never an adjective like "coarse" or "regional", so a tier's name stays true even if the
 * ladder itself ever grows another rung.
 *
 * This module owns ONLY the ladder and its resolution. The two frozen per-layer ladders it is
 * meant to eventually replace -- `SOIL_SURVEY_TIERS` (usda-soil.ts) and `SOIL_FIELD_TIERS`
 * (environmental-read-model.ts) -- are untouched here; repointing their consumers is a later
 * slice against a contract this module does not yet declare frozen.
 */

/** The four tiers every layer publishes at, ordered low to high. */
export const ZOOM_TIERS = [0, 5, 9, 13] as const;

/** One rung of the ladder above -- the literal union `ZOOM_TIERS` enumerates. */
export type ZoomTier = (typeof ZOOM_TIERS)[number];

/** Digits a rendered `zoom=` path segment is zero-padded to; must match `ZOOM_SEGMENT_DIGITS` in zoom.py. */
const ZOOM_SEGMENT_DIGITS = 2;

/**
 * Raised when a map zoom cannot be resolved to a published tier at all. Never returned as a
 * guessed tier: a caller that produced an impossible zoom (negative, non-finite) has a bug
 * upstream, and answering it with `z0` would hide that bug behind a path that happens to exist.
 */
export class ZoomTierResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ZoomTierResolutionError";
  }
}

/**
 * Resolve a MapLibre map zoom DOWN to the tier that serves it: the highest published rung at or
 * below the zoom (z11 resolves to z9, z13 and above resolves to z13, z3 resolves to z0).
 *
 * Fractional zooms (MapLibre reports e.g. 11.4) are compared directly against the integer
 * breakpoints with no rounding step -- flooring first would change nothing given integer
 * breakpoints, and would only invite a rounding-direction bug the Python side has no equivalent
 * of. There is no ceiling above the top tier: any zoom at or beyond it resolves to the top tier,
 * because that tier's partitions already answer every zoom up to the web-map maximum. A zoom
 * below the floor tier, or a non-finite value (`NaN`, `Infinity`, `-Infinity`), throws rather
 * than resolving to a guessed tier -- see `ZoomTierResolutionError`.
 */
export function resolveZoomTier(mapZoom: number): ZoomTier {
  if (!Number.isFinite(mapZoom)) {
    throw new ZoomTierResolutionError(
      `map zoom ${mapZoom} is not a finite number; no published tier can serve it`
    );
  }
  const floorTier = ZOOM_TIERS[0];
  if (mapZoom < floorTier) {
    throw new ZoomTierResolutionError(
      `map zoom ${mapZoom} is below the ladder's floor tier (z${floorTier}); no published partition serves it`
    );
  }
  let servingTier: ZoomTier = floorTier;
  for (const tier of ZOOM_TIERS) {
    if (tier <= mapZoom) servingTier = tier;
  }
  return servingTier;
}

/**
 * Render a tier into its `zoom=` partition path segment -- the one place the path string is
 * built, so no call site hand-interpolates a differently-padded literal. Zero-padded to
 * `ZOOM_SEGMENT_DIGITS` digits (`zoom=09`, not `zoom=9`) to match `zoom_prefix` in zoom.py.
 */
export function zoomTierPathSegment(tier: ZoomTier): string {
  return `zoom=${tier.toString().padStart(ZOOM_SEGMENT_DIGITS, "0")}`;
}

/**
 * The rung the warehouse writes from a lane's own source grain. Every other rung is DERIVED from
 * it, which is why it has no ladder resolution of its own -- see `DERIVED_TIER_CELL_DEGREES`.
 */
export const BASE_ZOOM_TIER = 13 satisfies ZoomTier;

/** Every rung derived from the base, finest first. Mirrors `DERIVED_ZOOM_TIERS` in tiers.py. */
export const DERIVED_ZOOM_TIERS = [9, 5, 0] as const satisfies readonly ZoomTier[];

/** One rung the derivation writes rather than the lane. */
export type DerivedZoomTier = (typeof DERIVED_ZOOM_TIERS)[number];

/**
 * THE TIER -> CELL SIZE TABLE. The grid each derived rung re-floors its lane onto, in degrees.
 *
 * Mirrors `TIER_RESOLUTION_DEGREES` in
 * `services/agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py` exactly. That
 * module sizes each rung at four web-map pixels of the tier's OWN zoom, rounded to a clean
 * decimal, and states the arithmetic beside the numbers:
 *
 *   z9  ->  4 * 360/(256*512)  = 0.0110 deg  ->  0.01
 *   z5  ->  4 * 360/(256*32)   = 0.1758 deg  ->  0.2
 *   z0  ->  4 * 360/256        = 5.6250 deg  ->  5.0
 *
 * The base rung is absent from that mapping and from this one, deliberately: it is not derived,
 * so it has no ladder resolution -- its grain is the lane's own (`LANE_BASE_LATTICES`).
 *
 * The two implementations must never diverge. A client that draws a rung at a pitch the writer
 * did not floor onto paints cells that overlap their neighbours or leave the background showing
 * between them, which is the exact defect the 2026-09-01 production assessment recorded.
 */
export const DERIVED_TIER_CELL_DEGREES: Readonly<Record<DerivedZoomTier, number>> = {
  9: 0.01,
  5: 0.2,
  0: 5.0,
};

/** The grid a rung re-floors onto; null at the base rung, whose grain belongs to the lane. */
export function cellSizeDegreesForTier(zoomTier: ZoomTier): number | null {
  return zoomTier === BASE_ZOOM_TIER ? null : DERIVED_TIER_CELL_DEGREES[zoomTier];
}

/**
 * How one lane's BASE rung locates a cell -- the grain and the phase every coarser rung inherits.
 *
 * `coordinateMeaning` is the difference between a cell drawn from its corner and one drawn around
 * its centre, and it is a property of the lane's own export SQL rather than of the ladder:
 * `fire_detections_day_export.sql` floor-snaps to a 0.005-degree grid and writes the ORIGIN, while
 * `signal_plane_day_export.sql` and `vegetation_day_export.sql` write `ST_X(cell.centroid)` and so
 * write the CENTRE. Getting it wrong shifts a whole field by half a cell.
 */
export interface LaneBaseLattice {
  /** The base rung's cell pitch in degrees; 0 for a lane whose base rows are genuine points. */
  cellSizeDegrees: number;
  /** Whether a base row's coordinates name the cell's south-west corner or its centre. */
  coordinateMeaning: "cell_origin" | "cell_center";
  /**
   * THE LANE'S PHASE: where a base row's own coordinate falls relative to whole multiples of
   * `cellSizeDegrees`, in degrees.
   *
   * Declared per lane rather than derived from `coordinateMeaning`, because the two centred lanes
   * on this ladder disagree about it and only their producers know which is which. The
   * quarter-degree lanes put their CENTROIDS a half step off the integer grid -- `cell_south =
   * row * 0.25` with the centre a half step above it (`ingest/vegetation.py:344-347`), and
   * `ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES = 0.125` with the westmost centroid at
   * `-124.875` (`pipeline/direct/soil/support.py:51-57`) -- so their centroids are odd multiples
   * of 0.125 and their cell EDGES are the multiples of 0.25. The one-degree climate lattice does
   * the opposite: `CLIMATE_FIELD_LATTICE_ROWS` steps by whole degrees, so its centres are on the
   * integers and its edges fall on the halves.
   *
   * Assuming the second shape for the first is a half-cell shift of every vegetation and
   * soil-field cell at every rung -- measured at z5 as 13 of 56 longitude columns holding two
   * measurements while 14 held none and drew as empty stripes of basemap.
   *
   * Zero for a `cell_origin` lane: its coordinates are already `floor(x / r) * r`, which is
   * anchored at zero by construction and has no phase to declare.
   */
  centroidOffsetDegrees: number;
}

/**
 * Each cell-bearing lane's base grain, cited to the producer that fixes it.
 *
 * `climate-field` is 1 degree and NOT the 0.5 degrees NASA POWER measures. The lane samples that
 * product on a one-degree lattice (`CLIMATE_FIELD_LATTICE_ROWS` steps by whole degrees), so a
 * half-degree cell leaves half the ground blank in each axis -- three quarters of the viewport --
 * which is the "separated rectangular climate blocks" the production assessment recorded. The
 * drawn cell is therefore the ground NEAREST its sample, which is what a tessellation of a regular
 * lattice means, and the measured support is carried in the caption instead of in the geometry.
 * Permitted because `LAYER_RENDER_CONTRACT` sets `declaredSupportDegrees: null` for these lanes
 * and already licenses `isoband`, which claims strictly more ground than a nearest-sample cell.
 * See `src/lib/server/services/AGENTS.md` "Tessellated support geometry".
 */
export const LANE_BASE_LATTICES = {
  /** `floor(longitude / 0.005) * 0.005`, fire_detections_day_export.sql:107. */
  "fire-detections": {
    cellSizeDegrees: 0.005,
    coordinateMeaning: "cell_origin",
    centroidOffsetDegrees: 0,
  },
  /**
   * `agri.spatial_cell` quarter-degree grid, written as its centroid -- and that centroid sits a
   * HALF STEP off the multiples of 0.25: `ingest/vegetation.py:344-347` builds each cell from
   * `cell_south = row * 0.25` and takes its centre as `cell_south + 0.25 * 0.5`.
   */
  vegetation: {
    cellSizeDegrees: 0.25,
    coordinateMeaning: "cell_center",
    centroidOffsetDegrees: 0.125,
  },
  /**
   * The same quarter-degree cells, on the same half-step phase: `pipeline/direct/soil/support.py`
   * pins `ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES = 0.125` and a westmost centroid of
   * `-124.875` (:51-57), so the edges are the multiples of 0.25 and `soilFieldPolygon`'s
   * long-standing `west = longitude - 0.125` was right.
   */
  "soil-field": {
    cellSizeDegrees: 0.25,
    coordinateMeaning: "cell_center",
    centroidOffsetDegrees: 0.125,
  },
  /**
   * The one-degree NASA POWER sampling lattice; see the note above. Phase 0, not a half step:
   * `CLIMATE_FIELD_LATTICE_ROWS` (parquet-climate-field.ts) steps by whole degrees, so the
   * SAMPLES are the integers and the drawn cell straddles each of them.
   */
  "climate-field": {
    cellSizeDegrees: 1,
    coordinateMeaning: "cell_center",
    centroidOffsetDegrees: 0,
  },
  /**
   * A gauge is a station, not a cell: it has no base grain at all, so every derived rung's cell is
   * the ladder's own and the base rung stays a raw point with no cell size.
   */
  "water-gauges": { cellSizeDegrees: 0, coordinateMeaning: "cell_origin", centroidOffsetDegrees: 0 },
  /**
   * The same shape as the gauges above, for the same reason: an Open-Meteo observation is a
   * sampled POINT, not a measured cell, so its base rung has no grain and every derived rung's
   * cell is the ladder's own.
   *
   * Whether the sampling lattice behind those points should itself be declared as a support --
   * the way `climate-field` declares its one-degree lattice -- is the open sampled-grid question
   * m0 still owes a ruling on. Until it rules, the honest answer is the one the contract already
   * gives: `weather` is an `event_point` layer, and a point claims no ground.
   */
  "weather-observations": {
    cellSizeDegrees: 0,
    coordinateMeaning: "cell_origin",
    centroidOffsetDegrees: 0,
  },
} as const satisfies Readonly<Record<string, LaneBaseLattice>>;

/** A lane with a published base grain. */
export type CellLaneId = keyof typeof LANE_BASE_LATTICES;

/**
 * The lattice one rung of one lane is actually served on: its pitch, its phase, and how to read a
 * served coordinate back onto it.
 */
export interface ServedCellLattice {
  /** The drawn cell's width and height in degrees. */
  cellSizeDegrees: number;
  /**
   * Where a lattice LINE falls relative to whole multiples of `cellSizeDegrees` -- the phase, not
   * the position of the sample inside its cell. Zero for the ladder's own grids (anchored at zero
   * by `floor(x / r) * r`) and for the quarter-degree lanes, whose centroids are already a half
   * step off the integer grid; minus half a cell only for the one-degree climate lattice, whose
   * samples sit ON the integers and so must be straddled. See `LaneBaseLattice.centroidOffsetDegrees`.
   */
  originOffsetDegrees: number;
  /**
   * Half the pitch of the grid the served coordinate was snapped onto, added back before the
   * index is taken. Zero when the coordinate is already the cell's centre.
   */
  snapCorrectionDegrees: number;
  /** What the served coordinates name, for the envelope this lattice is declared in. */
  origin: "cell_origin" | "cell_center";
}

/**
 * Where the LINES of a lane's own base grid fall, as an offset from whole multiples of its pitch.
 *
 * A centred lane's coordinate is its cell's middle, so the line below it is half a pitch under the
 * coordinate -- and the coordinate itself is `centroidOffsetDegrees` off the integer grid, so the
 * line lands at `centroidOffsetDegrees - cellSizeDegrees / 2`. That is 0 for the quarter-degree
 * lanes (0.125 - 0.125) and -0.5 for the one-degree climate lattice (0 - 0.5): the two shapes the
 * producers actually publish, from one expression rather than from a guess about which is normal.
 *
 * A `cell_origin` lane's coordinate is already a lattice line, so its offset is zero.
 */
function baseLatticeOffsetDegrees(base: LaneBaseLattice): number {
  return base.coordinateMeaning === "cell_center"
    ? base.centroidOffsetDegrees - base.cellSizeDegrees / 2
    : 0;
}

/**
 * The lattice a rung is served on, from the one tier table and the lane's own base grain.
 *
 * THE CELL IS NEVER FINER THAN THE LANE'S BASE. Below z13 the ladder's grid is 0.01 at z9 and 0.2
 * at z5, both FINER than the quarter-degree cells the signal, soil-field and vegetation lanes
 * publish. Re-flooring a quarter-degree measurement onto a 0.01 grid merges nothing, so those rungs
 * are a relabelling of the base rung, not a coarsening -- and drawing them at the ladder's pitch
 * would paint a 0.25-degree measurement as a 0.01-degree speck (the fictitious finer footprint the
 * track's spec forbids) or, at z5, as a 0.2-degree cell on a grid that leaves one lattice column in
 * five empty (visible background cracks, because 0.2 does not divide 0.25).
 *
 * Taking the coarser of the two removes both defects at once and is exactly true: a derived rung
 * cannot describe ground finer than the rung it was derived from.
 */
export function servedCellLattice(
  zoomTier: ZoomTier,
  base: LaneBaseLattice
): ServedCellLattice {
  const tierCellDegrees = cellSizeDegreesForTier(zoomTier);
  if (tierCellDegrees === null) {
    return {
      cellSizeDegrees: base.cellSizeDegrees,
      originOffsetDegrees: baseLatticeOffsetDegrees(base),
      snapCorrectionDegrees:
        base.coordinateMeaning === "cell_center" ? 0 : base.cellSizeDegrees / 2,
      origin: base.coordinateMeaning,
    };
  }
  const keepsBaseGrain = tierCellDegrees < base.cellSizeDegrees;
  return {
    cellSizeDegrees: keepsBaseGrain ? base.cellSizeDegrees : tierCellDegrees,
    // A rung that kept the base grain is still on the BASE lattice, phase included; one that
    // genuinely coarsened is on the ladder's grid, which is anchored at zero by `floor(x / r) * r`.
    originOffsetDegrees: keepsBaseGrain ? baseLatticeOffsetDegrees(base) : 0,
    snapCorrectionDegrees: tierCellDegrees / 2,
    // Every derived rung writes the floored cell origin -- see `GridAggregation` in tiers.py,
    // "the coordinate written back is the floored cell ORIGIN rather than its centre".
    origin: "cell_origin",
  };
}

/**
 * The lattice column or row a served coordinate falls in.
 *
 * A served coordinate is the cell's own centre, or a point somewhere inside the cell that the
 * derivation's `floor` moved by less than one tier grid step. Adding back half that step lands
 * within half a cell of the true centre in every case the ladder produces, so rounding recovers
 * the index exactly rather than approximately -- the flooring error is at most
 * `tierCellDegrees / 2`, and the cell is at least `tierCellDegrees` wide whenever the base grain
 * was kept, so the normalised error is strictly below the half-cell that would tip the rounding.
 */
export function latticeCellIndex(coordinate: number, lattice: ServedCellLattice): number {
  return Math.round(
    (coordinate + lattice.snapCorrectionDegrees - lattice.originOffsetDegrees) /
      lattice.cellSizeDegrees -
      0.5
  );
}

/**
 * The lattice cell that CONTAINS an arbitrary point, half-open on its upper edges.
 *
 * Not the same operation as `latticeCellIndex`, and the two must not be swapped. That one recovers
 * the cell a SERVED coordinate denotes, and it does so by adding back half the grid step the
 * derivation floored the coordinate by -- which is exactly wrong for a point that was never
 * floored, and lands it in the next cell up whenever it sits past the cell's midpoint. This one
 * takes a point that means only itself: a frozen lattice centre being counted against the rung
 * that would serve it, or any test of "which drawn cell covers here".
 */
export function latticeCellIndexContaining(
  coordinate: number,
  lattice: ServedCellLattice
): number {
  return Math.floor((coordinate - lattice.originOffsetDegrees) / lattice.cellSizeDegrees);
}

/**
 * One lattice cell's span, rebuilt from its integer index.
 *
 * BIT-IDENTICAL SHARED BOUNDARIES, and this is the whole reason the index exists. Cell `i`'s upper
 * edge and cell `i + 1`'s lower edge are the SAME expression over the same operands, so they are
 * the same double to the last bit -- where `coordinate + size` for one row and `coordinate` for its
 * neighbour are two different computations that agree only to within rounding, which is what draws
 * a hairline of background between two cells that are supposed to touch.
 */
export function latticeCellSpan(
  index: number,
  lattice: ServedCellLattice
): readonly [number, number] {
  return [
    lattice.originOffsetDegrees + index * lattice.cellSizeDegrees,
    lattice.originOffsetDegrees + (index + 1) * lattice.cellSizeDegrees,
  ];
}

/** The complete tessellating cell one served coordinate stands for, closed in GeoJSON order. */
export function tessellatedCellPolygon(
  longitude: number,
  latitude: number,
  lattice: ServedCellLattice
): GeoJSON.Polygon {
  const [west, east] = latticeCellSpan(latticeCellIndex(longitude, lattice), lattice);
  const [south, north] = latticeCellSpan(latticeCellIndex(latitude, lattice), lattice);
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
 * A stable support id for a row whose rung carries no cell identity of its own.
 *
 * Minted from the rung and the position rather than left null: `AggregateEnvelopeSupport.supportId`
 * is never null, because a client that had to read "aggregate" off a missing id could not tell a
 * rung whose rows happen to carry ids from raw observations.
 */
export function mintedSupportId(
  zoomTier: ZoomTier,
  longitude: number,
  latitude: number
): string {
  return `${zoomTier}:${longitude}:${latitude}`;
}
