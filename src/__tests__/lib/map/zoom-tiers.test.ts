import { describe, expect, it } from "vitest";
import {
  BASE_ZOOM_TIER,
  DERIVED_TIER_CELL_DEGREES,
  DERIVED_ZOOM_TIERS,
  LANE_BASE_LATTICES,
  ZOOM_TIERS,
  ZoomTierResolutionError,
  cellSizeDegreesForTier,
  latticeCellIndex,
  latticeCellSpan,
  mintedSupportId,
  resolveZoomTier,
  servedCellLattice,
  tessellatedCellPolygon,
  zoomTierPathSegment,
} from "@/lib/map/zoom-tiers";

/* --------------------------------------------------------------------------
 * The real quarter-degree lattice, as its producers pin it
 *
 * Not an invented coordinate near the right area: `pipeline/direct/soil/support.py:51-57` fixes
 * this grid at 56 longitudes from -124.875 stepping 0.25 and 28 latitudes from 42.125, giving
 * exactly 1,568 cells, and `ingest/vegetation.py:344-347` builds the vegetation cells the same way
 * (`cell_south = row * 0.25`, centre a half step above). Every soil-field and vegetation coordinate
 * in this file is drawn from it, because a coordinate that is on no real lattice cannot tell a
 * correct phase from an inverted one -- the previous `(-114.25, 43.5)` asserted a corner that is
 * itself a real centroid, so it passed with the phase half a cell wrong.
 * ----------------------------------------------------------------------- */

const LATTICE_STEP_DEGREES = 0.25;
const LATTICE_WEST_CENTROID = -124.875;
const LATTICE_SOUTH_CENTROID = 42.125;
const LATTICE_LONGITUDE_COUNT = 56;
const LATTICE_LATITUDE_COUNT = 28;
const LATTICE_CELL_COUNT = LATTICE_LONGITUDE_COUNT * LATTICE_LATITUDE_COUNT;

/** The south-west centroid of that lattice; its cell is [-125, -124.75] x [42, 42.25]. */
const PINNED_SOUTH_WEST_CENTROID = [LATTICE_WEST_CENTROID, LATTICE_SOUTH_CENTROID] as const;

/**
 * The producer's own floor, character for character: `floor_to_resolution` in
 * `warehouse/parquet/tiers.py:313-315` is `(values / resolution).floor() * resolution`, NOT
 * `v - v % r`, because the two disagree for negative values and every longitude here is negative.
 * A test that floored differently would test a rung the warehouse never wrote.
 */
function flooredToResolution(value: number, resolution: number): number {
  return Math.floor(value / resolution) * resolution;
}

/** Every centroid of the pinned lattice, in publication order. */
function pinnedLatticeCentroids(): readonly (readonly [number, number])[] {
  const centroids: (readonly [number, number])[] = [];
  for (let column = 0; column < LATTICE_LONGITUDE_COUNT; column += 1) {
    for (let row = 0; row < LATTICE_LATITUDE_COUNT; row += 1) {
      centroids.push([
        LATTICE_WEST_CENTROID + column * LATTICE_STEP_DEGREES,
        LATTICE_SOUTH_CENTROID + row * LATTICE_STEP_DEGREES,
      ]);
    }
  }
  return centroids;
}

describe("the uniform zoom ladder", () => {
  it("publishes exactly z0, z5, z9, z13 in ascending order", () => {
    expect(ZOOM_TIERS).toEqual([0, 5, 9, 13]);
  });
});

describe("resolveZoomTier at exact breakpoints", () => {
  it("resolves z0 to the z0 tier", () => {
    expect(resolveZoomTier(0)).toBe(0);
  });

  it("resolves z5 to the z5 tier", () => {
    expect(resolveZoomTier(5)).toBe(5);
  });

  it("resolves z9 to the z9 tier", () => {
    expect(resolveZoomTier(9)).toBe(9);
  });

  it("resolves z13 to the z13 tier", () => {
    expect(resolveZoomTier(13)).toBe(13);
  });
});

describe("resolveZoomTier just below each breakpoint", () => {
  it("resolves the value just under z5 to the z0 tier", () => {
    expect(resolveZoomTier(4.999)).toBe(0);
  });

  it("resolves the value just under z9 to the z5 tier", () => {
    expect(resolveZoomTier(8.999)).toBe(5);
  });

  it("resolves the value just under z13 to the z9 tier", () => {
    expect(resolveZoomTier(12.999)).toBe(9);
  });
});

describe("resolveZoomTier just above each breakpoint", () => {
  it("resolves the value just over z0 to the z0 tier", () => {
    expect(resolveZoomTier(0.001)).toBe(0);
  });

  it("resolves the value just over z5 to the z5 tier", () => {
    expect(resolveZoomTier(5.001)).toBe(5);
  });

  it("resolves the value just over z9 to the z9 tier", () => {
    expect(resolveZoomTier(9.001)).toBe(9);
  });

  it("resolves the value just over z13 to the z13 tier", () => {
    expect(resolveZoomTier(13.001)).toBe(13);
  });
});

describe("resolveZoomTier on ordinary fractional zooms", () => {
  it("resolves a mid-tier fractional zoom to its enclosing tier, matching the MapLibre-reported example", () => {
    expect(resolveZoomTier(11.4)).toBe(9);
  });

  it("resolves a fractional zoom below the first breakpoint to the floor tier", () => {
    expect(resolveZoomTier(3)).toBe(0);
  });
});

describe("resolveZoomTier above the top tier", () => {
  it("resolves a zoom far past the top tier to the top tier rather than throwing", () => {
    expect(resolveZoomTier(22)).toBe(13);
  });

  it("resolves an extreme zoom to the top tier", () => {
    expect(resolveZoomTier(100)).toBe(13);
  });
});

describe("resolveZoomTier below the floor tier", () => {
  it("throws for a negative zoom rather than silently serving z0", () => {
    expect(() => resolveZoomTier(-1)).toThrow(ZoomTierResolutionError);
  });

  it("throws for a small negative fractional zoom", () => {
    expect(() => resolveZoomTier(-0.001)).toThrow(ZoomTierResolutionError);
  });
});

describe("resolveZoomTier on non-finite input", () => {
  it("throws for NaN rather than silently serving a tier", () => {
    expect(() => resolveZoomTier(NaN)).toThrow(ZoomTierResolutionError);
  });

  it("throws for positive Infinity", () => {
    expect(() => resolveZoomTier(Infinity)).toThrow(ZoomTierResolutionError);
  });

  it("throws for negative Infinity", () => {
    expect(() => resolveZoomTier(-Infinity)).toThrow(ZoomTierResolutionError);
  });
});

describe("zoomTierPathSegment", () => {
  it("zero-pads single-digit tiers to two digits, matching zoom_prefix in zoom.py", () => {
    expect(zoomTierPathSegment(0)).toBe("zoom=00");
    expect(zoomTierPathSegment(5)).toBe("zoom=05");
    expect(zoomTierPathSegment(9)).toBe("zoom=09");
  });

  it("renders the two-digit top tier unpadded beyond its natural width", () => {
    expect(zoomTierPathSegment(13)).toBe("zoom=13");
  });
});


/**
 * THE PARITY TEST. These numbers are a mirror of a Python table, and this file is the only thing
 * standing between the two implementations.
 *
 * `TIER_RESOLUTION_DEGREES` in
 * `services/agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py` reads exactly
 * `{9: 0.01, 5: 0.2, 0: 5.0}`, sized at four web-map pixels of each tier's own zoom:
 * z9 -> 4 * 360/(256*512) = 0.0110 -> 0.01, z5 -> 4 * 360/(256*32) = 0.1758 -> 0.2,
 * z0 -> 4 * 360/256 = 5.6250 -> 5.0. `BASE_ZOOM_TIER` is 13 there too, and is deliberately absent
 * from that mapping: the base rung is not derived, so it has no ladder resolution.
 *
 * A drift here is not a rounding difference. The writer floors a lane onto its rung with these
 * numbers; a client that draws a different pitch paints cells that overlap their neighbours or
 * leave the map background showing between them.
 */
describe("the tier -> cell size table mirrors tiers.py", () => {
  it("pins the three derived resolutions to the exact Python literals", () => {
    expect(DERIVED_TIER_CELL_DEGREES).toEqual({ 9: 0.01, 5: 0.2, 0: 5.0 });
  });

  it("lists the derived rungs finest first, as DERIVED_ZOOM_TIERS does", () => {
    expect(DERIVED_ZOOM_TIERS).toEqual([9, 5, 0]);
    expect(DERIVED_ZOOM_TIERS).not.toContain(BASE_ZOOM_TIER);
  });

  it("gives the base rung no ladder resolution at all, rather than a guessed one", () => {
    expect(cellSizeDegreesForTier(13)).toBeNull();
    expect(cellSizeDegreesForTier(9)).toBe(0.01);
    expect(cellSizeDegreesForTier(5)).toBe(0.2);
    expect(cellSizeDegreesForTier(0)).toBe(5.0);
  });

  it("answers for every published rung", () => {
    for (const tier of ZOOM_TIERS) {
      const size = cellSizeDegreesForTier(tier);
      expect(tier === BASE_ZOOM_TIER ? size === null : (size ?? 0) > 0).toBe(true);
    }
  });
});

/**
 * The base grains, each cited to the producer that fixes it: fire floor-snaps to 0.005 and writes
 * the ORIGIN (`fire_detections_day_export.sql:107`), while the signal, soil-field and vegetation
 * lanes write `ST_X(cell.centroid)` of the quarter-degree `agri.spatial_cell` grid and so write
 * the CENTRE.
 */
describe("lane base lattices", () => {
  it("pins each lane's own grain and what its coordinates name", () => {
    expect(LANE_BASE_LATTICES["fire-detections"]).toEqual({
      cellSizeDegrees: 0.005,
      coordinateMeaning: "cell_origin",
      centroidOffsetDegrees: 0,
    });
    expect(LANE_BASE_LATTICES.vegetation.cellSizeDegrees).toBe(0.25);
    expect(LANE_BASE_LATTICES.vegetation.coordinateMeaning).toBe("cell_center");
    expect(LANE_BASE_LATTICES["soil-field"].cellSizeDegrees).toBe(0.25);
    expect(LANE_BASE_LATTICES["climate-field"].cellSizeDegrees).toBe(1);
    // A gauge is a station, so this lane has no base cell for a rung to inherit.
    expect(LANE_BASE_LATTICES["water-gauges"].cellSizeDegrees).toBe(0);
  });

  /**
   * The phase, which is a per-lane fact and not a consequence of `coordinateMeaning`. The two
   * quarter-degree lanes put their CENTROIDS a half step off the integer grid
   * (`ingest/vegetation.py:344-347`, `pipeline/direct/soil/support.py:51-57`), so their cell edges
   * are the multiples of 0.25; the one-degree climate lattice samples ON the whole degrees.
   */
  it("pins each centred lane's phase to the producer that fixes it", () => {
    expect(LANE_BASE_LATTICES.vegetation.centroidOffsetDegrees).toBe(0.125);
    expect(LANE_BASE_LATTICES["soil-field"].centroidOffsetDegrees).toBe(0.125);
    expect(LANE_BASE_LATTICES["climate-field"].centroidOffsetDegrees).toBe(0);
    // A floored origin is already a lattice line, so a `cell_origin` lane declares no phase.
    for (const lane of ["fire-detections", "water-gauges", "weather-observations"] as const) {
      expect(LANE_BASE_LATTICES[lane].centroidOffsetDegrees, lane).toBe(0);
    }
  });
});

describe("servedCellLattice", () => {
  it("never draws a cell finer than the lane's own base grain", () => {
    // 0.01 at z9 and 0.2 at z5 are both FINER than a quarter-degree measurement, so those rungs
    // are a relabelling of the base rung and keep its cell rather than shrinking it to a speck.
    for (const tier of [9, 5] as const) {
      expect(servedCellLattice(tier, LANE_BASE_LATTICES["soil-field"]).cellSizeDegrees).toBe(0.25);
    }
    // z0 genuinely coarsens: five degrees is far wider than the base cell.
    expect(servedCellLattice(0, LANE_BASE_LATTICES["soil-field"]).cellSizeDegrees).toBe(5.0);
  });

  it("uses the ladder's own grid for a lane whose base is finer than it", () => {
    expect(servedCellLattice(9, LANE_BASE_LATTICES["fire-detections"]).cellSizeDegrees).toBe(0.01);
    expect(servedCellLattice(5, LANE_BASE_LATTICES["fire-detections"]).cellSizeDegrees).toBe(0.2);
    expect(servedCellLattice(13, LANE_BASE_LATTICES["fire-detections"]).cellSizeDegrees).toBe(
      0.005
    );
  });

  it("declares the cell origin on every derived rung and the lane's own meaning at the base", () => {
    expect(servedCellLattice(13, LANE_BASE_LATTICES["soil-field"]).origin).toBe("cell_center");
    expect(servedCellLattice(13, LANE_BASE_LATTICES["fire-detections"]).origin).toBe("cell_origin");
    for (const tier of DERIVED_ZOOM_TIERS) {
      expect(servedCellLattice(tier, LANE_BASE_LATTICES["soil-field"]).origin).toBe("cell_origin");
    }
  });
});

describe("the tessellated cell one served coordinate stands for", () => {
  /**
   * The cell `soilFieldPolygon` has always drawn at the detail rung, to the same corner -- and now
   * for a centroid that is ON the lane's real lattice. `(-124.875, 42.125)` is the pinned south-west
   * centroid (`pipeline/direct/soil/support.py:51-57`); its cell is the quarter degree whose EDGES
   * are the multiples of 0.25, so the corner is `(-125, 42)` and not the neighbouring centroid.
   */
  it("centres a base-rung cell on the row's own coordinate", () => {
    const lattice = servedCellLattice(13, LANE_BASE_LATTICES["soil-field"]);
    const polygon = tessellatedCellPolygon(
      PINNED_SOUTH_WEST_CENTROID[0],
      PINNED_SOUTH_WEST_CENTROID[1],
      lattice
    );

    expect(polygon.coordinates[0][0]).toEqual([-125.0, 42.0]);
    expect(polygon.coordinates[0][2]).toEqual([-124.75, 42.25]);
  });

  /**
   * The recovery that makes z5 tessellate at all. The derivation floors a quarter-degree CENTRE
   * onto a 0.2 grid, and 0.2 does not divide 0.25 -- so the served coordinate is no longer on any
   * lattice, and drawing 0.2-degree cells from it leaves one column in five empty. Adding back
   * half the grid step lands within half a cell of the true centre, which is what lets the round
   * recover the base index exactly.
   */
  it("recovers the base cell from a z5 coordinate the derivation floored off-lattice", () => {
    const lattice = servedCellLattice(5, LANE_BASE_LATTICES["soil-field"]);
    // The z5 rung carries floor(centroid / 0.2) * 0.2, which is (-125, 42) for this centroid.
    const polygon = tessellatedCellPolygon(
      flooredToResolution(PINNED_SOUTH_WEST_CENTROID[0], 0.2),
      flooredToResolution(PINNED_SOUTH_WEST_CENTROID[1], 0.2),
      lattice
    );

    expect(polygon.coordinates[0][0]).toEqual([-125.0, 42.0]);
    expect(polygon.coordinates[0][2]).toEqual([-124.75, 42.25]);
  });

  it("recovers the base cell from a z9 coordinate floored onto the 0.01 grid", () => {
    const lattice = servedCellLattice(9, LANE_BASE_LATTICES["soil-field"]);
    const polygon = tessellatedCellPolygon(
      flooredToResolution(PINNED_SOUTH_WEST_CENTROID[0], 0.01),
      flooredToResolution(PINNED_SOUTH_WEST_CENTROID[1], 0.01),
      lattice
    );

    expect(polygon.coordinates[0][0]).toEqual([-125.0, 42.0]);
    expect(polygon.coordinates[0][2]).toEqual([-124.75, 42.25]);
  });

  /**
   * The acceptance gate "neighboring cells share bit-identical boundaries". `toBe` rather than
   * `toBeCloseTo` on purpose: a hairline of map background between two cells that should touch is
   * exactly what a nearly-equal edge draws.
   */
  it("gives two adjacent cells the SAME double on their shared edge", () => {
    for (const tier of ZOOM_TIERS) {
      const lattice = servedCellLattice(tier, LANE_BASE_LATTICES["soil-field"]);
      const index = latticeCellIndex(PINNED_SOUTH_WEST_CENTROID[1], lattice);
      const [, upperEdge] = latticeCellSpan(index, lattice);
      const [lowerEdgeOfNeighbour] = latticeCellSpan(index + 1, lattice);

      expect(upperEdge).toBe(lowerEdgeOfNeighbour);
    }
  });

  it("tiles a run of adjacent cells with no gap and no overlap", () => {
    const lattice = servedCellLattice(0, LANE_BASE_LATTICES["climate-field"]);
    const spans = [0, 1, 2, 3].map((step) =>
      latticeCellSpan(latticeCellIndex(-115 + step * 5, lattice), lattice)
    );

    for (let position = 1; position < spans.length; position += 1) {
      expect(spans[position][0]).toBe(spans[position - 1][1]);
    }
  });

  it("closes every ring in GeoJSON position order", () => {
    const lattice = servedCellLattice(13, LANE_BASE_LATTICES["climate-field"]);
    const ring = tessellatedCellPolygon(-115, 43, lattice).coordinates[0];

    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
  });
});

describe("mintedSupportId", () => {
  it("names the rung and the position, so two rungs cannot mint one id", () => {
    expect(mintedSupportId(5, -115, 43)).toBe("5:-115:43");
    expect(mintedSupportId(13, -115, 43)).not.toBe(mintedSupportId(9, -115, 43));
  });
});

describe("every rung of every cell lane resolves to a drawable cell", () => {
  it("gives a positive cell size to each cell-bearing lane at each rung", () => {
    const cellLanes = ["fire-detections", "vegetation", "soil-field", "climate-field"] as const;
    for (const lane of cellLanes) {
      for (const tier of ZOOM_TIERS) {
        expect(servedCellLattice(tier, LANE_BASE_LATTICES[lane]).cellSizeDegrees).toBeGreaterThan(
          0
        );
      }
    }
  });
});

/* --------------------------------------------------------------------------
 * The domain sweep: every real cell of the quarter-degree lattice, at every rung that draws it
 *
 * The test the inverted phase would have failed and the old point tests could not. It builds all
 * 1,568 pinned centroids, pushes each through the producer's own floor for the rung
 * (`floor_to_resolution`, tiers.py:313-315), draws the square the client would draw for the
 * coordinate that comes out, and then asks the three questions a tessellation has to answer:
 * every measurement gets its own square, the squares leave no hole, and each square is the one
 * its measurement is INSIDE. With the phase half a cell off, z5 answered 43 longitude columns
 * instead of 56 -- 13 squares holding two measurements each and 13 columns of bare basemap.
 *
 * z0 is deliberately absent: five degrees genuinely coarsens a quarter-degree lattice, so many
 * centroids SHOULD share one cell there. Collisions are the correct answer at that rung and a
 * defect at the other three.
 * ----------------------------------------------------------------------- */
describe("the whole pinned lattice tessellates at every rung that keeps its grain", () => {
  const RUNGS_THAT_KEEP_THE_BASE_GRAIN = [13, 9, 5] as const;

  for (const lane of ["soil-field", "vegetation"] as const) {
    for (const tier of RUNGS_THAT_KEEP_THE_BASE_GRAIN) {
      it(`draws ${LATTICE_CELL_COUNT} distinct squares for ${lane} at z${tier}`, () => {
        const lattice = servedCellLattice(tier, LANE_BASE_LATTICES[lane]);
        const resolution = cellSizeDegreesForTier(tier);
        const squaresByCorner = new Map<string, number>();

        for (const [longitude, latitude] of pinnedLatticeCentroids()) {
          // What the rung actually carries: the base rung carries the centroid, every derived
          // rung carries that centroid floored onto the ladder's grid.
          const servedLongitude =
            resolution === null ? longitude : flooredToResolution(longitude, resolution);
          const servedLatitude =
            resolution === null ? latitude : flooredToResolution(latitude, resolution);
          const [west, south] =
            tessellatedCellPolygon(servedLongitude, servedLatitude, lattice).coordinates[0][0];
          const [east, north] =
            tessellatedCellPolygon(servedLongitude, servedLatitude, lattice).coordinates[0][2];

          // The square is the one the measurement is in, not the neighbour half a cell away.
          expect(
            longitude >= west && longitude < east && latitude >= south && latitude < north,
            `${lane} z${tier}: (${longitude}, ${latitude}) is outside its own square ` +
              `[${west}, ${east}] x [${south}, ${north}]`
          ).toBe(true);

          const corner = `${west}:${south}`;
          squaresByCorner.set(corner, (squaresByCorner.get(corner) ?? 0) + 1);
        }

        const collided = [...squaresByCorner.values()].filter((count) => count > 1);
        expect(collided, `${lane} z${tier}: squares holding more than one measurement`).toEqual([]);
        expect(squaresByCorner.size).toBe(LATTICE_CELL_COUNT);

        // No interior gap: the distinct west edges are a contiguous run of the cell pitch, and so
        // are the south edges, and the two runs multiply out to every square drawn above.
        const wests = [...new Set([...squaresByCorner.keys()].map((key) => Number(key.split(":")[0])))].sort(
          (left, right) => left - right
        );
        const souths = [...new Set([...squaresByCorner.keys()].map((key) => Number(key.split(":")[1])))].sort(
          (left, right) => left - right
        );
        expect(wests).toHaveLength(LATTICE_LONGITUDE_COUNT);
        expect(souths).toHaveLength(LATTICE_LATITUDE_COUNT);
        for (const edges of [wests, souths]) {
          for (let position = 1; position < edges.length; position += 1) {
            expect(
              edges[position] - edges[position - 1],
              `${lane} z${tier}: gap between ${edges[position - 1]} and ${edges[position]}`
            ).toBeCloseTo(lattice.cellSizeDegrees, 9);
          }
        }
        expect(wests.length * souths.length).toBe(squaresByCorner.size);
      });
    }
  }

  /**
   * The other half of the phase claim: the CLIMATE lane samples on the whole degrees, so its
   * one-degree cell straddles its sample rather than starting at it. Fixing the quarter-degree
   * lanes must not move this one.
   */
  it("leaves the one-degree climate lattice straddling its whole-degree samples", () => {
    for (const tier of [13, 9, 5] as const) {
      const lattice = servedCellLattice(tier, LANE_BASE_LATTICES["climate-field"]);
      expect(lattice.originOffsetDegrees, `climate z${tier}`).toBe(-0.5);
    }
    const detail = servedCellLattice(13, LANE_BASE_LATTICES["climate-field"]);
    expect(tessellatedCellPolygon(-115, 43, detail).coordinates[0][0]).toEqual([-115.5, 42.5]);
  });

  /** And fire, whose 0.005-degree ORIGINS are already lattice lines at every rung. */
  it("leaves the fire lane anchored at zero at every rung", () => {
    for (const tier of ZOOM_TIERS) {
      expect(
        servedCellLattice(tier, LANE_BASE_LATTICES["fire-detections"]).originOffsetDegrees,
        `fire z${tier}`
      ).toBe(0);
    }
  });
});
