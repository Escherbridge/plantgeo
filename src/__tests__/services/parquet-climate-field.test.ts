import { describe, expect, it } from "vitest";
import {
  climateFieldLatticeCellCount,
  parquetClimateFieldCollection,
} from "@/lib/server/services/parquet-climate-field";
import { LANE_BASE_LATTICES, servedCellLattice, type ZoomTier } from "@/lib/map/zoom-tiers";
import { UnpermittedRenderFormError } from "@/lib/map/layer-render-contract";
import type { ParquetClimateFieldObservation } from "@/lib/server/services/parquet-trpc-readers";

/** The support envelope the reader now attaches to every row, built the way the reader builds it. */
function support(zoomTier: ZoomTier, cellId: string | null, longitude: number, latitude: number) {
  const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES["climate-field"]);
  return {
    zoomTier,
    supportKind: "tessellated_cell" as const,
    supportId: cellId ?? `${zoomTier}:${longitude}:${latitude}`,
    origin: lattice.origin,
    cellWidthDegrees: lattice.cellSizeDegrees,
    cellHeightDegrees: lattice.cellSizeDegrees,
    aggregationMethod: "mean" as const,
    contributorCount: 2,
    provenance: {
      sourceLayer: "climate-field-precipitation",
      observedDay: "2026-08-06",
      newestObservedAt: "2026-08-06T00:00:00Z",
      attribution: "NASA POWER (NASA LaRC)",
    },
  };
}

/** One stored cell at the rung named, with the identity rule that rung publishes. */
function row(
  zoomTier: ZoomTier,
  longitude = -115,
  latitude = 43,
  value = 2.5
): ParquetClimateFieldObservation {
  const cellId = zoomTier === 13 ? `cell-${longitude}-${latitude}` : null;
  return {
    cellId,
    observedDay: "2026-08-06",
    value,
    observationCount: 2,
    newestObservedAt: "2026-08-06T00:00:00Z",
    coverageFraction: 1,
    allowedClientExposure: false,
    longitude,
    latitude,
    support: support(zoomTier, cellId, longitude, latitude),
  };
}

function ready(data: readonly ParquetClimateFieldObservation[]) {
  return {
    state: "ready" as const,
    requestedDay: "2026-08-06",
    servedDay: "2026-08-06",
    data,
    truncated: false,
  };
}

/** Every ring of a feature, whatever its geometry type. */
function ringsOf(feature: GeoJSON.Feature): number[][][] {
  if (feature.geometry.type === "Polygon") return feature.geometry.coordinates;
  if (feature.geometry.type === "MultiPolygon") return feature.geometry.coordinates.flat();
  return [];
}

/** Shoelace area of one closed ring, signed. */
function ringArea(ring: number[][]): number {
  let total = 0;
  for (let position = 0; position < ring.length - 1; position += 1) {
    total +=
      ring[position][0] * ring[position + 1][1] - ring[position + 1][0] * ring[position][1];
  }
  return total / 2;
}

describe("Parquet climate-field collection adapter", () => {
  it("preserves the existing GeoJSON contract from an exact Parquet day", () => {
    const collection = parquetClimateFieldCollection(
      ready([row(13)]),
      "precipitation",
      "mean",
      "-116.1,42.9,-114.9,44.1",
      13,
      "field"
    );

    expect(collection).toMatchObject({
      availability: "published",
      signal: "precipitation",
      unit: "mm/day",
      observedDay: "2026-08-06",
      requestedDay: "2026-08-06",
      cellCount: 1,
      latticeCellCount: 4,
      maxObservationAgeDays: 0,
      sourceClientExposureApproved: false,
      granularity: "detail",
      zoomTier: 13,
    });
    expect(collection.features[0]).toMatchObject({
      id: "cell--115-43",
      geometry: { type: "Polygon" },
      properties: { value: 2.5, observedDay: "2026-08-06", aggregated: false },
    });
  });

  it("keeps a named warehouse gap distinct from an upstream failure", () => {
    const collection = parquetClimateFieldCollection(
      { state: "not_generated", requestedDay: "2026-08-07", reason: "day_not_written" },
      "precipitation",
      "mean",
      "-116.1,42.9,-114.9,44.1",
      13
    );

    expect(collection).toMatchObject({
      availability: "unavailable",
      reason: "not_published",
      requestedDay: "2026-08-07",
      latticeCellCount: 4,
      features: [],
      // Declared even when nothing was drawn: an empty collection still has to say which rung was
      // asked and at what pitch, or the renderer cannot tell "no cells" from "no rung".
      zoomTier: 13,
    });
    expect(collection.support).toMatchObject({
      zoomTier: 13,
      supportKind: "tessellated_cell",
      cellWidthDegrees: 1,
      contributorCount: 0,
    });
  });

  /**
   * The wave-1 behaviour this replaces: every rung below z13 was served as POINTS, because the
   * module was not told the coarse lattice pitch and would not guess one. It comes from the shared
   * tier table now, so a coarse rung draws the ground it stands for instead of a dot on it.
   */
  it.each([
    [0, "coarse-average", 5],
    [5, "coarse-average", 1],
    [9, "regional-average", 1],
  ] as const)(
    "draws the z%s aggregate as a %s tessellation of %s-degree cells",
    (zoomTier, granularity, cellDegrees) => {
      const collection = parquetClimateFieldCollection(
        ready([row(zoomTier)]),
        "precipitation",
        "mean",
        "-116.1,42.9,-114.9,44.1",
        zoomTier,
        "field"
      );

      expect(collection).toMatchObject({ granularity, zoomTier, renderForm: "field" });
      expect(collection.support).toMatchObject({
        zoomTier,
        supportKind: "tessellated_cell",
        origin: "cell_origin",
        cellWidthDegrees: cellDegrees,
        cellHeightDegrees: cellDegrees,
        aggregationMethod: "mean",
        contributorCount: 2,
      });
      const [feature] = collection.features;
      expect(feature.geometry.type).toBe("Polygon");
      // Read off the DECLARED rung, never off a null cell id, and keyed on the row's own support
      // id so the feature survives a pan.
      expect(feature.properties).toMatchObject({ aggregated: true, cellKey: null });
      expect(feature.id).toBe(`${zoomTier}:-115:43`);
      expect(Math.abs(ringArea(ringsOf(feature)[0]))).toBeCloseTo(cellDegrees * cellDegrees, 9);
    }
  );

  /**
   * The acceptance gate "neighboring cells share bit-identical boundaries and no map background
   * appears through cracks", at the two rungs the assessment called out. `toBe` rather than
   * `toBeCloseTo`: a nearly-equal edge is exactly what draws a hairline of background.
   */
  it.each([9, 5] as const)(
    "gives two adjacent z%s cells a bit-identical shared boundary",
    (zoomTier) => {
      const collection = parquetClimateFieldCollection(
        ready([row(zoomTier, -115, 43), row(zoomTier, -114, 43)]),
        "precipitation",
        "mean",
        "-116.1,42.9,-113.9,44.1",
        zoomTier,
        "field"
      );

      const [west, east] = collection.features.map((feature) => ringsOf(feature)[0]);
      const westEasternEdge = Math.max(...west.map(([longitude]) => longitude));
      const eastWesternEdge = Math.min(...east.map(([longitude]) => longitude));

      expect(westEasternEdge).toBe(eastWesternEdge);
    }
  );

  /**
   * The other half of the same gate: the cells do not merely touch, they FILL. Two adjacent cells
   * cover exactly the extent from one's west edge to the other's east edge, with no crack between
   * them and no double cover.
   */
  it("fills a run of adjacent cells with no gap and no overlap", () => {
    const collection = parquetClimateFieldCollection(
      ready([row(13, -115, 43), row(13, -114, 43), row(13, -113, 43)]),
      "precipitation",
      "mean",
      "-116.1,42.9,-112.9,44.1",
      13,
      "field"
    );

    const rings = collection.features.map((feature) => ringsOf(feature)[0]);
    const covered = rings.reduce((total, ring) => total + Math.abs(ringArea(ring)), 0);
    const longitudes = rings.flat().map(([longitude]) => longitude);
    const latitudes = rings.flat().map(([, latitude]) => latitude);
    const extent =
      (Math.max(...longitudes) - Math.min(...longitudes)) *
      (Math.max(...latitudes) - Math.min(...latitudes));

    expect(covered).toBeCloseTo(extent, 9);
  });

  /**
   * `latticeCellCount` is now measured on the SERVED rung rather than published only for the
   * detail one. Wave 1 sent 0 below z13, which put "of the 0 cells in view" on screen.
   */
  it("counts the denominator on the rung that answered, at every rung", () => {
    const bbox = "-116.1,42.9,-114.9,44.1";
    expect(climateFieldLatticeCellCount(bbox, 13)).toBe(4);
    expect(climateFieldLatticeCellCount(bbox, 9)).toBe(4);
    expect(climateFieldLatticeCellCount(bbox, 5)).toBe(4);
    // The four one-degree samples in view straddle a five-degree boundary: -116 falls in
    // [-120, -115) and -115 falls in [-115, -110), so the rung really does draw two cells.
    expect(climateFieldLatticeCellCount(bbox, 0)).toBe(2);
  });

  it("uses centers rather than intersecting cell footprints at viewport edges", () => {
    const bbox = "-115.75,42.75,-114.25,43.25";
    const collection = parquetClimateFieldCollection(
      ready([row(13)]),
      "precipitation",
      "mean",
      bbox,
      13,
      "field"
    );

    expect(climateFieldLatticeCellCount(bbox, 13)).toBe(1);
    expect(collection).toMatchObject({ cellCount: 1, latticeCellCount: 1 });
  });

  it("includes a lattice center exactly on the east and north bbox corner", () => {
    const bbox = "-115.5,42.5,-115,43";
    const collection = parquetClimateFieldCollection(
      ready([row(13)]),
      "precipitation",
      "mean",
      bbox,
      13,
      "field"
    );

    expect(climateFieldLatticeCellCount(bbox, 13)).toBe(1);
    expect(collection).toMatchObject({ cellCount: 1, latticeCellCount: 1 });
  });

  it("counts the full frozen lattice and ordinary partial viewports by center", () => {
    expect(climateFieldLatticeCellCount("-180,-90,180,90", 13)).toBe(397);
    expect(climateFieldLatticeCellCount("-116.1,42.9,-114.9,44.1", 13)).toBe(4);
  });

  /**
   * `LAYER_RENDER_CONTRACT` permits `isoband` at the coarse and middle bands only: a band asserts
   * the field varies smoothly BETWEEN samples, and the detail rung serves those samples. So the
   * one degrade left runs the other way from wave 1's -- zoom OUT for contours.
   */
  it("degrades a contour to the filled tessellation at the detail rung", () => {
    const collection = parquetClimateFieldCollection(
      ready([row(13)]),
      "dew-point",
      "mean",
      "-116.1,42.9,-114.9,44.1",
      13,
      "isoline"
    );

    expect(collection.renderForm).toBe("field");
    expect(collection.support.supportKind).toBe("tessellated_cell");
  });

  /**
   * Dissolved over the SERVED rung's lattice, not the detail one. Handing `buildIsobands` the
   * wrong step makes it read a regular lattice as a scatter: every square fails its corner test,
   * and the band comes back empty or in pieces -- a seam wherever one batch of rows met the next.
   */
  it("dissolves bands over the served rung's lattice, not the detail lattice", () => {
    const lattice = servedCellLattice(9, LANE_BASE_LATTICES["climate-field"]);
    const samples = [-115, -114, -113].flatMap((longitude) =>
      [43, 44, 45].map((latitude) => row(9, longitude, latitude, 12))
    );
    const collection = parquetClimateFieldCollection(
      ready(samples),
      "dew-point",
      "mean",
      "-116.1,42.9,-112.9,45.1",
      9,
      "isoline"
    );

    expect(collection.renderForm).toBe("isoline");
    expect(collection.support).toMatchObject({
      supportKind: "isoband",
      aggregationMethod: "dissolve",
      cellWidthDegrees: lattice.cellSizeDegrees,
    });
    // One uniform value over a 3x3 lattice dissolves to ONE ring covering the whole 2x2 extent.
    // A wrong step would drop every square and leave nothing to draw.
    expect(collection.features).toHaveLength(1);
    expect(Math.abs(ringArea(ringsOf(collection.features[0])[0]))).toBeCloseTo(4, 9);
  });
});

/**
 * The contract is a RULE at presentation time, not a description of one.
 *
 * `cellFeatures` chooses between the cell each row declares and an `isoband` dissolved across
 * them, and until 2026-09-02 nothing checked either choice against `LAYER_RENDER_CONTRACT`: every
 * permitted-form lookup in the tree was reached only from tests. A row whose envelope declares a
 * form the rung does not permit now refuses to draw rather than painting a shape the contract
 * never licensed -- `raw_point` is permitted on no band of a continuous field, because a dot at a
 * sample's centre claims a footprint finer than the ground the lane measured.
 */
describe("the drawn form is checked against the contract", () => {
  it("refuses a row whose envelope declares a form no band of a continuous field permits", () => {
    const declared = row(13);
    const offending = {
      ...declared,
      support: { ...declared.support, supportKind: "raw_point" as const },
    };

    expect(() =>
      parquetClimateFieldCollection(
        ready([offending]),
        "precipitation",
        "mean",
        "-116.1,42.9,-114.9,44.1",
        13,
        "field"
      )
    ).toThrow(UnpermittedRenderFormError);
  });

  it("draws the tessellated cell the reader really declares, at every rung", () => {
    for (const zoomTier of [0, 5, 9, 13] as ZoomTier[]) {
      expect(() =>
        parquetClimateFieldCollection(
          ready([row(zoomTier)]),
          "precipitation",
          "mean",
          "-116.1,42.9,-114.9,44.1",
          zoomTier,
          "field"
        )
      ).not.toThrow();
    }
  });
});
