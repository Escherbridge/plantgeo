import { describe, expect, it } from "vitest";
import {
  climateFieldLatticeCellCount,
  parquetClimateFieldCollection,
} from "@/lib/server/services/parquet-climate-field";

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
      "-116.1,42.9,-114.9,44.1"
    );

    expect(collection).toMatchObject({
      availability: "unavailable",
      reason: "not_published",
      requestedDay: "2026-08-07",
      latticeCellCount: 4,
      features: [],
    });
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
