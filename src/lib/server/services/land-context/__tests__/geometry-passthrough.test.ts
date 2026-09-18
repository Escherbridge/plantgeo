/**
 * The bounded readers must carry `boundary.geometryWkb` from the storage
 * layer into `sourceFeature.geometryWkb` untouched -- present WKB survives,
 * a null stays null (never a fabricated shape). The real Parquet lane does
 * not exist yet, so the storage layer is mocked here with the exact
 * `CandidateBoundaryFeature` shape `parquet-reader.ts` promises; when the
 * placeholder bodies are replaced this file is what proves the geometry
 * reaches the router without further reader changes.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CandidateBoundaryFeature } from "@/lib/server/services/land-context/parquet-reader";

const storage = vi.hoisted(() => ({ features: [] as unknown[] }));

vi.mock("@/lib/server/services/land-context/parquet-reader", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/server/services/land-context/parquet-reader")>();
  return {
    ...actual,
    findContainingFeatures: vi.fn(async () => ({ features: storage.features, gap: "", refusal: null })),
    pruneCandidatesByBbox: vi.fn(async () => ({ candidateKeys: ["candidate"], gap: "", refusal: null })),
    exactIntersectCandidates: vi.fn(async () => ({ features: storage.features, gap: "" })),
    findBoundaryByParcelKey: vi.fn(async () => ({
      feature: storage.features[0] ?? null,
      gap: "",
      refusal: null,
    })),
  };
});

import {
  readBoundaryByParcelKey,
  readBoundedAoiIntersection,
  readPointContainment,
} from "@/lib/server/services/land-context/reader";

const POINT_WKB = "0101000000000000000000f03f0000000000000040";

function candidate(geometryWkb: string | null): CandidateBoundaryFeature {
  return {
    boundary: {
      sourceNamespace: "blm-national-sma",
      nativeFeatureKey: "SMA-000123",
      nativeFeatureVersion: null,
      familyType: "blm_surface_management",
      interestType: "surface_management",
      state: "ID",
      county: "Ada",
      geometryWkb,
    },
    overlapBasis: { kind: "point_containment", description: "point inside polygon" },
    sourceRelease: {
      publisher: "Bureau of Land Management",
      canonicalEndpoint: "https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer",
      sourceVersion: "2026-09",
      captureTime: null,
      sourceEffectiveTime: null,
      sourcePublishedTime: null,
      admissionVerdict: "admitted",
    },
  };
}

beforeEach(() => {
  storage.features = [];
});

describe("readers carry geometryWkb from storage into sourceFeature", () => {
  it("readPointContainment keeps present WKB on the matched result", async () => {
    storage.features = [candidate(POINT_WKB)];
    const response = await readPointContainment(-116.2, 43.6);
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data).toHaveLength(1);
    expect(response.data[0].coverageState).toBe("matched");
    expect(response.data[0].sourceFeature?.geometryWkb).toBe(POINT_WKB);
  });

  it("readPointContainment keeps a null WKB null rather than inventing one", async () => {
    storage.features = [candidate(null)];
    const response = await readPointContainment(-116.2, 43.6);
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data[0].coverageState).toBe("matched");
    expect(response.data[0].sourceFeature?.geometryWkb).toBeNull();
  });

  it("readBoundedAoiIntersection keeps present WKB on every matched result", async () => {
    storage.features = [candidate(POINT_WKB), candidate(null)];
    const response = await readBoundedAoiIntersection({ west: -117, south: 43, east: -116, north: 44 });
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data.map((result) => result.sourceFeature?.geometryWkb)).toEqual([POINT_WKB, null]);
  });

  it("readBoundaryByParcelKey keeps present WKB on the single result", async () => {
    storage.features = [candidate(POINT_WKB)];
    const response = await readBoundaryByParcelKey({
      sourceNamespace: "blm-national-sma",
      originalId: "SMA-000123",
      state: "ID",
    });
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data.sourceFeature?.geometryWkb).toBe(POINT_WKB);
  });
});
