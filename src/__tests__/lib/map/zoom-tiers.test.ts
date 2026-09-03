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
    });
    expect(LANE_BASE_LATTICES.vegetation.cellSizeDegrees).toBe(0.25);
    expect(LANE_BASE_LATTICES.vegetation.coordinateMeaning).toBe("cell_center");
    expect(LANE_BASE_LATTICES["soil-field"].cellSizeDegrees).toBe(0.25);
    expect(LANE_BASE_LATTICES["climate-field"].cellSizeDegrees).toBe(1);
    // A gauge is a station, so this lane has no base cell for a rung to inherit.
    expect(LANE_BASE_LATTICES["water-gauges"].cellSizeDegrees).toBe(0);
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
  /** The cell `soilFieldPolygon` has always drawn at the detail rung, to the same corner. */
  it("centres a base-rung cell on the row's own coordinate", () => {
    const lattice = servedCellLattice(13, LANE_BASE_LATTICES["soil-field"]);
    const polygon = tessellatedCellPolygon(-114.25, 43.5, lattice);

    expect(polygon.coordinates[0][0]).toEqual([-114.375, 43.375]);
    expect(polygon.coordinates[0][2]).toEqual([-114.125, 43.625]);
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
    // floor(43.5 / 0.2) * 0.2 = 43.4, the latitude the z5 rung actually carries.
    const polygon = tessellatedCellPolygon(-114.4, 43.4, lattice);

    expect(polygon.coordinates[0][0]).toEqual([-114.375, 43.375]);
    expect(polygon.coordinates[0][2]).toEqual([-114.125, 43.625]);
  });

  it("recovers the base cell from a z9 coordinate floored onto the 0.01 grid", () => {
    const lattice = servedCellLattice(9, LANE_BASE_LATTICES["soil-field"]);

    expect(tessellatedCellPolygon(-114.25, 43.5, lattice).coordinates[0][0]).toEqual([
      -114.375, 43.375,
    ]);
  });

  /**
   * The acceptance gate "neighboring cells share bit-identical boundaries". `toBe` rather than
   * `toBeCloseTo` on purpose: a hairline of map background between two cells that should touch is
   * exactly what a nearly-equal edge draws.
   */
  it("gives two adjacent cells the SAME double on their shared edge", () => {
    for (const tier of ZOOM_TIERS) {
      const lattice = servedCellLattice(tier, LANE_BASE_LATTICES["soil-field"]);
      const index = latticeCellIndex(43.5, lattice);
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
