import { describe, expect, it } from "vitest";
import {
  climateFieldLatticeCellCount,
  parquetClimateFieldCollection,
} from "@/lib/server/services/parquet-climate-field";
import { LANE_BASE_LATTICES, servedCellLattice, type ZoomTier } from "@/lib/map/zoom-tiers";
import { UnpermittedRenderFormError } from "@/lib/map/layer-render-contract";
import type { ParquetClimateFieldObservation } from "@/lib/server/services/parquet-trpc-readers";
import { climateFieldReadBbox, climateGeometryOverlapsViewport } from "@/lib/map/climate-viewport";

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

/** One stored cell; derived-rung coordinates must already be floored origins. */
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
  it.each([false, true])("preserves publication and truncation on an empty viewport (%s)", (truncated) => {
    expect(parquetClimateFieldCollection({ ...ready([]), truncated }, "precipitation", "mean", "-116,43,-115,44", 13)).toMatchObject({
      availability: "published", renderStatus: "no_cells_in_view", reason: null, observedDay: "2026-08-06", truncated,
      parquet: { state: "published", servedDay: "2026-08-06" }, features: [],
    });
  });

  it.each(["day_not_written", "lane_never_written"] as const)("preserves %s as its own terminal reason", (reason) => {
    expect(parquetClimateFieldCollection({ state: "not_generated", requestedDay: "2026-08-06", reason }, "precipitation", "mean", "-116,43,-115,44", 13)).toMatchObject({
      parquet: { state: reason, servedDay: null }, availability: "unavailable", renderStatus: "unavailable", features: [],
    });
  });

  it("retains governed absence evidence and its served day", () => {
    const evidence = { reason: "No source observations", upstreamResponse: "200 []", recordedAt: "2026-08-07T00:00:00Z", runId: "absence-1" };
    expect(parquetClimateFieldCollection({ state: "absent", requestedDay: "2026-08-06", servedDay: "2026-08-06", evidence }, "precipitation", "mean", "-116,43,-115,44", 13)).toMatchObject({
      parquet: { state: "governed_absence", servedDay: "2026-08-06", evidence }, availability: "unavailable", renderStatus: "unavailable", features: [],
    });
  });

  it("retains published source cells and truncation when contour construction has no polygons", () => {
    expect(parquetClimateFieldCollection({ ...ready([row(9)]), truncated: true }, "air-temperature", "mean", "-116,43,-114,44", 9, "isoline")).toMatchObject({
      parquet: { state: "published", servedDay: "2026-08-06" }, availability: "published", renderStatus: "insufficient_contour_neighbors", cellCount: 1, truncated: true,
    });
  });
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
    [0, "coarse-average", 5, 40],
    [5, "coarse-average", 1, 43],
    [9, "regional-average", 1, 43],
  ] as const)(
    "draws the z%s aggregate as a %s tessellation of %s-degree cells",
    (zoomTier, granularity, cellDegrees, storedLatitude) => {
      const collection = parquetClimateFieldCollection(
        ready([row(zoomTier, -115, storedLatitude)]),
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
      expect(feature.id).toBe(`${zoomTier}:-115:${storedLatitude}`);
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

  it("counts overlapping support footprints even when their centers are outside the viewport", () => {
    const bbox = "-115.75,42.75,-114.25,43.25";
    const collection = parquetClimateFieldCollection(
      ready([row(13)]),
      "precipitation",
      "mean",
      bbox,
      13,
      "field"
    );

    expect(climateFieldLatticeCellCount(bbox, 13)).toBe(3);
    expect(collection).toMatchObject({ cellCount: 1, latticeCellCount: 3 });
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

  it("counts the full frozen lattice and ordinary partial viewports by support overlap", () => {
    expect(climateFieldLatticeCellCount("-180,-90,180,90", 13)).toBe(397);
    expect(climateFieldLatticeCellCount("-116.1,42.9,-114.9,44.1", 13)).toBe(4);
  });

  it.each([[13, 43], [9, 43], [5, 43], [0, 40]] as const)("retains a supported z%s cell in a view between sample centers", (zoomTier, storedLatitude) => {
    const collection = parquetClimateFieldCollection(
      ready([row(zoomTier, -115, storedLatitude), row(zoomTier, -120, storedLatitude)]),
      "precipitation", "mean", "-114.9,43.1,-114.8,43.2", zoomTier, "field"
    );
    expect(collection).toMatchObject({ availability: "published", renderStatus: "drawn", cellCount: 1, latticeCellCount: 1 });
    expect(collection.features).toHaveLength(1);
    if (zoomTier === 0) {
      expect(collection.features[0].geometry).toEqual({
        type: "Polygon",
        coordinates: [[[-115, 40], [-110, 40], [-110, 45], [-115, 45], [-115, 40]]],
      });
    }
  });

  it("includes contour neighbors outside the visible support count", () => {
    const samples = [-116, -115, -114].flatMap((longitude) =>
      [42, 43, 44].map((latitude) => row(9, longitude, latitude, 12))
    );
    const collection = parquetClimateFieldCollection(
      ready(samples), "dew-point", "mean", "-114.9,43.1,-114.8,43.2", 9, "isoline"
    );
    expect(collection).toMatchObject({ availability: "published", renderStatus: "drawn", cellCount: 1, latticeCellCount: 1 });
    expect(collection.features).toHaveLength(1);
    expect(Math.abs(ringArea(ringsOf(collection.features[0])[0]))).toBeCloseTo(4, 9);
  });

  it("keeps a ready day and its samples published when contours lack neighboring corners", () => {
    const collection = parquetClimateFieldCollection(
      ready([row(9)]), "relative-humidity", "mean", "-114.9,43.1,-114.8,43.2", 9, "isoline"
    );
    expect(collection).toMatchObject({
      availability: "published", reason: null, renderStatus: "insufficient_contour_neighbors",
      observedDay: "2026-08-06", cellCount: 1, features: [],
    });
  });

  it("does not claim a visible contour when only the halo contains constructed bands", () => {
    const samples = [-115, -114].flatMap((longitude) =>
      [43, 44].map((latitude) => row(9, longitude, latitude, 12))
    );
    const collection = parquetClimateFieldCollection(
      ready(samples), "dew-point", "mean", "-113.8,43.1,-113.7,43.2", 9, "isoline"
    );
    expect(collection).toMatchObject({
      availability: "published", renderStatus: "insufficient_contour_neighbors", cellCount: 1, features: [],
    });
  });

  it("does not turn an empty viewport on a published day into an unpublished day", () => {
    const collection = parquetClimateFieldCollection(
      ready([]), "relative-humidity", "mean", "-103.4,43.1,-103.3,43.2", 13, "field"
    );
    expect(collection).toMatchObject({
      availability: "published", reason: null, renderStatus: "no_cells_in_view",
      observedDay: "2026-08-06", cellCount: 0, latticeCellCount: 0, features: [],
    });
  });

  it("keeps the eastern sample's footprint without extending the published lattice", () => {
    const overlapping = parquetClimateFieldCollection(
      ready([row(13, -104, 43)]), "precipitation", "mean", "-103.7,43.1,-103.6,43.2", 13, "field"
    );
    const outside = parquetClimateFieldCollection(
      ready([row(13, -104, 43)]), "precipitation", "mean", "-103.4,43.1,-103.3,43.2", 13, "field"
    );
    expect(overlapping).toMatchObject({ cellCount: 1, latticeCellCount: 1, renderStatus: "drawn" });
    expect(outside).toMatchObject({ cellCount: 0, latticeCellCount: 0, renderStatus: "no_cells_in_view", features: [] });
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

describe("bounded climate read halo", () => {
  it("does not treat a contour hole or an edge-only touch as visible area", () => {
    const geometry: GeoJSON.Polygon = {
      type: "Polygon", coordinates: [
        [[-116, 42], [-114, 42], [-114, 44], [-116, 44], [-116, 42]],
        [[-115.8, 42.2], [-115.8, 43.8], [-114.2, 43.8], [-114.2, 42.2], [-115.8, 42.2]],
      ],
    };
    expect(climateGeometryOverlapsViewport(geometry, [-115.1, 42.9, -114.9, 43.1])).toBe(false);
    expect(climateGeometryOverlapsViewport(geometry, [-114, 42, -113, 43])).toBe(false);
    expect(climateGeometryOverlapsViewport(geometry, [-116.1, 42.9, -115.9, 43.1])).toBe(true);
  });

  it.each([13, 9, 5] as const)("adds a one-degree neighbor halo at z%s", (zoomTier) => {
    expect(climateFieldReadBbox("-114.9,43.1,-114.8,43.2", zoomTier)).toBe("-115.9,42.1,-113.8,44.2");
  });

  it("uses the served five-degree pitch at the coarse rung", () => {
    expect(climateFieldReadBbox("-114.9,43.1,-114.8,43.2", 0)).toBe("-119.9,38.1,-109.8,48.2");
  });

  it("bounds the halo to valid WGS84 coordinates", () => {
    expect(climateFieldReadBbox("-179,-89,179,89", 0)).toBe("-180,-90,180,90");
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
