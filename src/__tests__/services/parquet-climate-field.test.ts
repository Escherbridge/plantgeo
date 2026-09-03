import { describe, expect, it } from "vitest";
import {
  climateFieldLatticeCellCount,
  parquetClimateFieldCollection,
} from "@/lib/server/services/parquet-climate-field";

/** The detail rung: stored cells, each with the identity the warehouse gave it. */
const rows = [
  {
    cellId: "cell-a",
    observedDay: "2026-08-06",
    value: 2.5,
    observationCount: 2,
    newestObservedAt: "2026-08-06T00:00:00Z",
    coverageFraction: 1,
    allowedClientExposure: false,
    longitude: -115,
    latitude: 43,
  },
];

/** A coarse rung: the same reading as an anonymous aggregate, with no identity to name. */
const aggregateRows = rows.map((row) => ({ ...row, cellId: null }));

describe("Parquet climate-field collection adapter", () => {
  it("preserves the existing GeoJSON contract from an exact Parquet day", () => {
    const collection = parquetClimateFieldCollection(
      {
        state: "ready",
        requestedDay: "2026-08-06",
        servedDay: "2026-08-06",
        data: rows,
        truncated: false,
      },
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
      id: "cell-a",
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
      // Declared even when nothing was drawn: an empty collection still has to say which rung it
      // asked, or the renderer cannot tell "no stored cells" from "no aggregate".
      zoomTier: 13,
    });
  });

  /**
   * A coarse rung aggregates onto its own lattice, whose pitch this module is not told. Drawing it
   * as a 0.5-degree square would paint a wide-area mean as one stored NASA POWER cell; points at
   * the aggregate's own centre need no pitch and claim no footprint. The degraded form is reported
   * back in `renderForm` so a client cannot mistake it for the one it asked for.
   */
  it.each([
    [0, "coarse-average"],
    [5, "coarse-average"],
    [9, "regional-average"],
  ] as const)("draws the z%s aggregate as %s points, never as detail squares", (
    zoomTier,
    granularity
  ) => {
    const collection = parquetClimateFieldCollection(
      {
        state: "ready",
        requestedDay: "2026-08-06",
        servedDay: "2026-08-06",
        data: aggregateRows,
        truncated: false,
      },
      "precipitation",
      "mean",
      "-116.1,42.9,-114.9,44.1",
      zoomTier,
      "field"
    );

    expect(collection).toMatchObject({ granularity, zoomTier, renderForm: "symbol" });
    expect(collection.features[0]).toMatchObject({
      geometry: { type: "Point", coordinates: [-115, 43] },
      // Read off the null identity rather than off the tier, so the flag and the identity can
      // never disagree, and keyed on the position so the feature survives a pan.
      properties: { aggregated: true, cellKey: null },
      id: "-115:43",
    });
  });

  /**
   * `latticeCellCount` counts the frozen 397-cell detail lattice. A coarse rung's cells are not
   * drawn from it, so publishing it there would put a numerator and a denominator measured on two
   * different lattices next to each other in the panel's "N of 397 cells in view" sentence.
   */
  it("publishes the detail lattice denominator only for the detail rung", () => {
    const collection = parquetClimateFieldCollection(
      {
        state: "ready",
        requestedDay: "2026-08-06",
        servedDay: "2026-08-06",
        data: aggregateRows,
        truncated: false,
      },
      "precipitation",
      "mean",
      "-116.1,42.9,-114.9,44.1",
      5,
      "field"
    );

    expect(collection.latticeCellCount).toBe(0);
    expect(collection.cellCount).toBe(1);
  });

  it("uses centers rather than intersecting cell footprints at viewport edges", () => {
    const bbox = "-115.75,42.75,-114.25,43.25";
    const collection = parquetClimateFieldCollection(
      {
        state: "ready",
        requestedDay: "2026-08-06",
        servedDay: "2026-08-06",
        data: rows,
        truncated: false,
      },
      "precipitation",
      "mean",
      bbox,
      13,
      "field"
    );

    expect(climateFieldLatticeCellCount(bbox)).toBe(1);
    expect(collection).toMatchObject({ cellCount: 1, latticeCellCount: 1 });
  });

  it("includes a lattice center exactly on the east and north bbox corner", () => {
    const bbox = "-115.5,42.5,-115,43";
    const collection = parquetClimateFieldCollection(
      {
        state: "ready",
        requestedDay: "2026-08-06",
        servedDay: "2026-08-06",
        data: rows,
        truncated: false,
      },
      "precipitation",
      "mean",
      bbox,
      13,
      "field"
    );

    expect(climateFieldLatticeCellCount(bbox)).toBe(1);
    expect(collection).toMatchObject({ cellCount: 1, latticeCellCount: 1 });
  });

  it("counts the full frozen lattice and ordinary partial viewports by center", () => {
    expect(climateFieldLatticeCellCount("-180,-90,180,90")).toBe(397);
    expect(climateFieldLatticeCellCount("-116.1,42.9,-114.9,44.1")).toBe(4);
  });
});
