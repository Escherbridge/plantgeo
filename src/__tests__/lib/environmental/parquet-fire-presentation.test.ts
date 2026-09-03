import { describe, expect, it } from "vitest";
import {
  fireDetectionTotals,
  presentParquetFireDetections,
  servingZoomTierForMapZoom,
} from "@/lib/environmental/parquet-fire-presentation";
import {
  UnpermittedRenderFormError,
  type AggregateEnvelopeSupport,
} from "@/lib/map/layer-render-contract";
import {
  LANE_BASE_LATTICES,
  latticeCellIndex,
  latticeCellSpan,
  mintedSupportId,
  servedCellLattice,
  tessellatedCellPolygon,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import type {
  ParquetFireDetectionCell,
  ParquetFireWindow,
  ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";

const CELL_LONGITUDE = -116.25;
const CELL_LATITUDE = 43.5;

/** The lattice `cellSupport` reads a fire rung on -- the lane's own base grain, or the ladder's. */
function fireLattice(zoomTier: ZoomTier) {
  return servedCellLattice(zoomTier, LANE_BASE_LATTICES["fire-detections"]);
}

/**
 * The envelope the fire reader actually declares at a rung, built from the same two published
 * tables the reader builds it from.
 *
 * Every field is the REAL one and none is a round number chosen for legibility: the cell is 0.01
 * degrees at z9 and 0.2 at z5, not the 0.25 this fixture asserted until 2026-09-02, and the id is
 * `mintedSupportId`'s `"9:-116.25:43.5"` rather than a hand-written `"z9:…"` the reader never
 * emits. A fixture that describes a shape no reader produces cannot fail when the reader changes.
 */
function support(
  zoomTier: ZoomTier,
  overrides: Partial<AggregateEnvelopeSupport> = {}
): AggregateEnvelopeSupport {
  const lattice = fireLattice(zoomTier);
  return {
    zoomTier,
    supportKind: "aggregate_cell",
    supportId: mintedSupportId(zoomTier, CELL_LONGITUDE, CELL_LATITUDE),
    origin: lattice.origin,
    cellWidthDegrees: lattice.cellSizeDegrees,
    cellHeightDegrees: lattice.cellSizeDegrees,
    cellOriginDegrees: [
      latticeCellSpan(latticeCellIndex(CELL_LONGITUDE, lattice), lattice)[0],
      latticeCellSpan(latticeCellIndex(CELL_LATITUDE, lattice), lattice)[0],
    ],
    aggregationMethod: "count",
    contributorCount: 4,
    provenance: {
      sourceLayer: "fire_detections",
      observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T19:12:00Z",
      attribution: "NASA FIRMS",
    },
    ...overrides,
  };
}

function cell(overrides: Partial<ParquetFireDetectionCell> = {}): ParquetFireDetectionCell {
  return {
    longitude: CELL_LONGITUDE,
    latitude: CELL_LATITUDE,
    observedDay: "2026-08-28",
    detectionCount: 4,
    frpSum: 120.5,
    frpObservationCount: 4,
    highConfidenceDetectionCount: 2,
    newestObservedAt: "2026-08-28T19:12:00Z",
    support: support(9),
    ...overrides,
  };
}

function readyWindow(
  cells: ParquetFireDetectionCell[],
  truncated = false
): ParquetReaderResult<ParquetFireWindow> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    truncated,
    data: { firstDay: "2026-08-28", lastDay: "2026-08-28", cells, days: [] },
  };
}

/** Every state that is NOT a window of cells; each must present as empty, never as a zero row. */
const TERMINAL_STATES: ParquetReaderResult<ParquetFireWindow>[] = [
  {
    state: "absent",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    evidence: {
      reason: "source_empty",
      upstreamResponse: "200 with no rows",
      recordedAt: "2026-08-28T02:00:00Z",
      runId: "run-1",
    },
  },
  { state: "not_generated", requestedDay: "2026-08-28", reason: "day_not_written" },
  { state: "not_generated", requestedDay: "2026-08-28", reason: "lane_never_written" },
  {
    state: "upstream_unavailable",
    fault: { kind: "network", message: "connection reset" },
  },
];

describe("presentParquetFireDetections", () => {
  it("draws the declared square at a middle rung and writes only the cell vocabulary", () => {
    const presented = presentParquetFireDetections(readyWindow([cell()]));

    expect(presented.features).toHaveLength(1);
    const [feature] = presented.features;
    // The SERVER's own square, not merely a square: `tessellatedCellPolygon` is what the serving
    // side draws for this coordinate at this rung, and the presenter must land on the same one.
    // Asserted against the writer-side builder rather than against a literal, because a literal
    // cannot catch the two implementations drifting apart -- which is exactly what they had done
    // at z9 and z5 before `cellOriginDegrees` put the lattice phase on the wire.
    expect(feature.geometry).toEqual(
      tessellatedCellPolygon(CELL_LONGITUDE, CELL_LATITUDE, fireLattice(9))
    );
    // The exact key set: an incident field creeping back in is what this pins. Every
    // expression in FireLayer reads one of these eleven and nothing else.
    expect(Object.keys(feature.properties).sort()).toEqual([
      "cellHeightDegrees",
      "cellWidthDegrees",
      "detectionCount",
      "frpObservationCount",
      "frpSum",
      "highConfidenceDetectionCount",
      "newestObservedAt",
      "observedDay",
      "supportId",
      "supportKind",
      "zoomTier",
    ]);
    expect(feature.properties).toEqual({
      detectionCount: 4,
      frpSum: 120.5,
      frpObservationCount: 4,
      highConfidenceDetectionCount: 2,
      observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T19:12:00Z",
      zoomTier: 9,
      supportKind: "aggregate_cell",
      // `mintedSupportId`'s format, spelled out once: the rung, then the position, with no `z`
      // prefix. The lane publishes no cell identity at any rung, so this is the only id there is.
      supportId: "9:-116.25:43.5",
      // The ladder's z9 grid, from DERIVED_TIER_CELL_DEGREES -- not the lane's 0.005 base grain
      // and not the round 0.25 this fixture used to claim.
      cellWidthDegrees: 0.01,
      cellHeightDegrees: 0.01,
    });
  });

  it("keeps the marker at the detail rung, where the count-scaled dot is the legible form", () => {
    const presented = presentParquetFireDetections(readyWindow([cell({ support: support(13) })]));

    expect(presented.features[0].geometry).toEqual({
      type: "Point",
      coordinates: [-116.25, 43.5],
    });
    // Still a declared aggregate, and still labelled with the rung: the shape changed, the
    // truth claim did not. FIRMS publishes no raw rung, so there is no z13 observation to
    // promote this to.
    expect(presented.features[0].properties.supportKind).toBe("aggregate_cell");
    expect(presented.features[0].properties.zoomTier).toBe(13);
  });

  it("gives adjacent coarse cells bit-identical shared edges", () => {
    // Two NEIGHBOURING z5 cells, one lattice step apart, at the pitch the ladder actually serves
    // that rung on. Stepping by the real cell size rather than by a hand-picked 0.25 is what makes
    // this a test of adjacency instead of a test of two unrelated squares.
    const lattice = fireLattice(5);
    const eastLongitude = CELL_LONGITUDE + lattice.cellSizeDegrees;
    const presented = presentParquetFireDetections(
      readyWindow([
        cell({ longitude: CELL_LONGITUDE, support: support(5) }),
        cell({
          longitude: eastLongitude,
          support: support(5, {
            supportId: mintedSupportId(5, eastLongitude, CELL_LATITUDE),
            cellOriginDegrees: [
              latticeCellSpan(latticeCellIndex(eastLongitude, lattice), lattice)[0],
              latticeCellSpan(latticeCellIndex(CELL_LATITUDE, lattice), lattice)[0],
            ],
          }),
        }),
      ])
    );

    const [west, east] = presented.features.map(
      (feature) => (feature.geometry as GeoJSON.Polygon).coordinates[0]
    );
    // The west cell's east edge and the east cell's west edge, to the bit -- no map background
    // can show through a seam whose two sides are the same number. `Object.is` as well as `toBe`:
    // what this guards is a one-ULP disagreement, invisible to any rounded comparison.
    expect(west[1][0]).toBe(east[0][0]);
    expect(Object.is(west[2][0], east[3][0])).toBe(true);
  });

  it("draws a marker rather than a guessed square when the envelope declares no size", () => {
    const presented = presentParquetFireDetections(
      readyWindow([
        cell({
          support: support(5, {
            cellWidthDegrees: undefined,
            cellHeightDegrees: undefined,
            cellOriginDegrees: undefined,
          }),
        }),
      ])
    );

    // A fabricated square is the fire-perimeter claim this track exists to prevent, so the
    // absence of a declared size degrades to the marker rather than to a nominal cell size.
    // The envelope itself is no longer optional -- `ParquetBrowserFireDetectionCell.support` is
    // required as of 2026-09-02 -- so this is the only route left to a marker above the detail
    // band, and it is one a reader has to declare rather than one it can fall into by omission.
    expect(presented.features[0].geometry.type).toBe("Point");
    expect(presented.features[0].properties.cellWidthDegrees).toBeNull();
    // Still a declared aggregate: what is missing is the footprint, not the claim.
    expect(presented.features[0].properties.supportKind).toBe("aggregate_cell");
    expect(presented.features[0].properties.zoomTier).toBe(5);
  });

  it("keeps an unreported FRP null rather than coercing it to zero megawatts", () => {
    const presented = presentParquetFireDetections(
      readyWindow([cell({ frpSum: null, frpObservationCount: 0, support: support(13) })])
    );

    // No reported power is not zero power. `FireLayer` branches on the observation count for
    // exactly this reason, and it can only do so while the null survives presentation.
    expect(presented.features[0].properties.frpSum).toBeNull();
    expect(presented.features[0].properties.frpObservationCount).toBe(0);
  });

  it("names each cell's rung from its own envelope and from nothing else", () => {
    // The envelope is the cell's own claim about how it was aggregated. A retained frame holds
    // cells from the rung that actually landed, and relabelling them with the rung of a request
    // still in flight would state an aggregation nothing performed -- which is why the caller's
    // latched rung stopped being passed to this function at all on 2026-09-02.
    const presented = presentParquetFireDetections(
      readyWindow([cell({ support: support(0) }), cell({ support: support(0) })])
    );

    expect(presented.features.map((feature) => feature.properties.zoomTier)).toEqual([0, 0]);
  });

  it("draws nothing for every state that is not a served window", () => {
    for (const result of TERMINAL_STATES) {
      expect(presentParquetFireDetections(result).features, result.state).toEqual([]);
    }
    expect(presentParquetFireDetections(undefined).features).toEqual([]);
  });
});

describe("fireDetectionTotals", () => {
  it("counts detections and cells separately, because above z13 they are different numbers", () => {
    const totals = fireDetectionTotals(
      readyWindow([
        cell({ detectionCount: 4, highConfidenceDetectionCount: 2 }),
        cell({ detectionCount: 11, highConfidenceDetectionCount: 0 }),
      ])
    );

    expect(totals).toEqual({
      detectionCount: 15,
      cellCount: 2,
      highConfidenceDetectionCount: 2,
    });
  });

  it("returns zeros only for a served window, leaving every refusal to the caller", () => {
    // The zeros here are indistinguishable from a genuinely empty day BY DESIGN: a caller must
    // read `state` before rendering these numbers, which is why FireDetails switches on it
    // first and prints an em dash for each refusal.
    expect(fireDetectionTotals(readyWindow([]))).toEqual({
      detectionCount: 0,
      cellCount: 0,
      highConfidenceDetectionCount: 0,
    });
    for (const result of TERMINAL_STATES) {
      expect(fireDetectionTotals(result).cellCount, result.state).toBe(0);
    }
  });
});

/**
 * The contract is a RULE at presentation time, not a description of one.
 *
 * `LAYER_RENDER_CONTRACT.fire` permits `aggregate_cell` at every band and nothing else at the
 * detail one, and until 2026-09-02 nothing in production ever asked it: every permitted-form
 * lookup was reached only from tests. A cell whose envelope declares a form the rung does not
 * permit now refuses to draw, because the alternative is a picture that misstates what was
 * measured, and a picture is not something a reader can audit.
 */
describe("the drawn form is checked against the contract", () => {
  it("refuses a cell whose envelope declares a form the rung does not permit", () => {
    expect(() =>
      presentParquetFireDetections(
        readyWindow([cell({ support: support(13, { supportKind: "raw_point" }) })])
      )
    ).toThrow(UnpermittedRenderFormError);
  });

  it("draws the form the reader really declares, at every rung", () => {
    for (const zoomTier of [0, 5, 9, 13] as ZoomTier[]) {
      expect(() =>
        presentParquetFireDetections(readyWindow([cell({ support: support(zoomTier) })]))
      ).not.toThrow();
    }
  });
});

/**
 * NOTHING IS CREATED OR LOST BETWEEN RUNGS.
 *
 * A coarse fire cell says "n hotspots were detected in this square", and the only thing that makes
 * that sentence true is that n is the sum of the detail cells the square covers -- no contributor
 * counted twice at a shared edge, none dropped between two squares. The fold below is the
 * producer's own: `floor_to_resolution` in `warehouse/parquet/tiers.py:313-315` is
 * `(v / r).floor() * r`, NOT `v - v % r`, because the two disagree for the negative longitudes
 * this warehouse is made of.
 *
 * The presenter does not aggregate -- the warehouse does -- so what this pins is the other half:
 * the square the client DRAWS for a folded row really is the ground its contributors fell in, and
 * the count it carries is theirs. A phase error in the drawn square shows up here as a detail cell
 * that is inside no drawn square, or inside two.
 */
describe("a coarse cell conserves the detail cells it covers", () => {
  const COARSE_RESOLUTION_DEGREES = 0.2;

  /** `floor_to_resolution`, tiers.py:313-315. */
  function flooredToResolution(value: number, resolution: number): number {
    return Math.floor(value / resolution) * resolution;
  }

  /** The base rung's own cells: 0.005-degree ORIGINS, the grain `fire_detections_day_export.sql` writes. */
  const DETAIL_CELLS = [
    { longitude: -116.25, latitude: 43.5, detectionCount: 4 },
    { longitude: -116.245, latitude: 43.5, detectionCount: 1 },
    { longitude: -116.1, latitude: 43.505, detectionCount: 7 },
    { longitude: -116.055, latitude: 43.695, detectionCount: 2 },
    { longitude: -115.995, latitude: 43.7, detectionCount: 5 },
    { longitude: -115.8, latitude: 43.9, detectionCount: 3 },
  ] as const;

  /** One coarse row, exactly as `cellSupport` builds its envelope at the rung. */
  function coarseCell(
    longitude: number,
    latitude: number,
    detectionCount: number
  ): ParquetFireDetectionCell {
    const lattice = fireLattice(5);
    return {
      longitude,
      latitude,
      observedDay: "2026-08-28",
      detectionCount,
      frpSum: null,
      frpObservationCount: 0,
      highConfidenceDetectionCount: 0,
      newestObservedAt: "2026-08-28T19:12:00Z",
      support: {
        zoomTier: 5,
        supportKind: "aggregate_cell",
        supportId: mintedSupportId(5, longitude, latitude),
        origin: lattice.origin,
        cellWidthDegrees: lattice.cellSizeDegrees,
        cellHeightDegrees: lattice.cellSizeDegrees,
        cellOriginDegrees: [
          latticeCellSpan(latticeCellIndex(longitude, lattice), lattice)[0],
          latticeCellSpan(latticeCellIndex(latitude, lattice), lattice)[0],
        ],
        aggregationMethod: "count",
        contributorCount: detectionCount,
        provenance: {
          sourceLayer: "fire_detections",
          observedDay: "2026-08-28",
          newestObservedAt: "2026-08-28T19:12:00Z",
          attribution: "NASA FIRMS",
        },
      },
    };
  }

  /** The warehouse's own derivation: fold the detail cells onto the rung's grid and SUM. */
  function foldedToCoarseRung(): ParquetFireDetectionCell[] {
    const summed = new Map<string, { longitude: number; latitude: number; count: number }>();
    for (const detail of DETAIL_CELLS) {
      const longitude = flooredToResolution(detail.longitude, COARSE_RESOLUTION_DEGREES);
      const latitude = flooredToResolution(detail.latitude, COARSE_RESOLUTION_DEGREES);
      const key = `${longitude}:${latitude}`;
      const existing = summed.get(key) ?? { longitude, latitude, count: 0 };
      existing.count += detail.detectionCount;
      summed.set(key, existing);
    }
    return [...summed.values()].map((folded) =>
      coarseCell(folded.longitude, folded.latitude, folded.count)
    );
  }

  /** The drawn square's own bounds, read back off the ring the presenter emitted. */
  function boundsOf(geometry: GeoJSON.Geometry) {
    if (geometry.type !== "Polygon") throw new Error(`expected a Polygon, got ${geometry.type}`);
    const ring = geometry.coordinates[0];
    const longitudes = ring.map(([longitude]) => longitude);
    const latitudes = ring.map(([, latitude]) => latitude);
    return {
      west: Math.min(...longitudes),
      east: Math.max(...longitudes),
      south: Math.min(...latitudes),
      north: Math.max(...latitudes),
    };
  }

  it("carries exactly the sum of the detail cells its drawn square covers", () => {
    const presented = presentParquetFireDetections(readyWindow(foldedToCoarseRung()));

    let coveredDetailCells = 0;
    for (const feature of presented.features) {
      const { west, east, south, north } = boundsOf(feature.geometry);
      const covered = DETAIL_CELLS.filter(
        (detail) =>
          detail.longitude >= west &&
          detail.longitude < east &&
          detail.latitude >= south &&
          detail.latitude < north
      );
      coveredDetailCells += covered.length;
      expect(covered.length, `${west},${south} covers no detail cell`).toBeGreaterThan(0);
      expect(
        feature.properties?.detectionCount,
        `${west},${south} does not carry its contributors' sum`
      ).toBe(covered.reduce((total, detail) => total + detail.detectionCount, 0));
    }

    // Every detail cell is inside exactly one drawn square: none dropped, none double-counted.
    expect(coveredDetailCells).toBe(DETAIL_CELLS.length);
    const drawnTotal = presented.features.reduce(
      (total, feature) => total + Number(feature.properties?.detectionCount ?? 0),
      0
    );
    expect(drawnTotal).toBe(
      DETAIL_CELLS.reduce((total, detail) => total + detail.detectionCount, 0)
    );
  });
});

describe("servingZoomTierForMapZoom", () => {
  it("resolves a map zoom down to the rung that serves it", () => {
    expect(servingZoomTierForMapZoom(3)).toBe(0);
    expect(servingZoomTierForMapZoom(11.4)).toBe(9);
    expect(servingZoomTierForMapZoom(13)).toBe(13);
    expect(servingZoomTierForMapZoom(22)).toBe(13);
  });

  it("names no rung for a zoom no rung serves, instead of throwing through a render", () => {
    // `resolveZoomTier` raises on these, which is right for a server read and wrong inside a
    // React render: the hook disables the query instead, and a thrown error would have blanked
    // the whole map subtree.
    expect(servingZoomTierForMapZoom(Number.NaN)).toBeNull();
    expect(servingZoomTierForMapZoom(-1)).toBeNull();
    expect(servingZoomTierForMapZoom(Number.POSITIVE_INFINITY)).toBeNull();
  });
});
