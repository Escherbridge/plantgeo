/**
 * The `landContext` boundary procedures ship a server-decoded `geometry`
 * beside every contract field. Browser code cannot import the WKB decoder
 * (`scripts/check-client-server-imports.mjs`), so this is the one place the
 * decode happens; against the placeholder storage layer the geometry is
 * `null` and the reader's gap string still travels through untouched.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CandidateBoundaryFeature } from "@/lib/server/services/land-context/parquet-reader";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

// `parquet-reader.ts` now issues a real pointer GET against the warehouse coverage census
// before falling through to the placeholder gap; mock it to an EMPTY warehouse so the
// "placeholder storage layer" test path never reaches the network, per the same pattern as
// `coverage-state.test.ts`.
const { getParquetWarehouseCoverage } = vi.hoisted(() => ({
  getParquetWarehouseCoverage: vi.fn(async () => ({
    coverageSchemaVersion: 1,
    generatedAt: "2026-09-18T00:00:00Z",
    evaluatedThroughDay: "2026-09-18",
    lanes: [],
  })),
}));

vi.mock("@/lib/server/services/parquet-plane-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/parquet-plane-client")>()),
  getParquetWarehouseCoverage,
}));

const storage = vi.hoisted(() => ({ features: [] as unknown[] }));

vi.mock("@/lib/server/services/land-context/parquet-reader", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/server/services/land-context/parquet-reader")>();
  return {
    ...actual,
    // Injected candidates when a test supplies them; otherwise the real placeholder, so the
    // verbatim "no Parquet lane wired in yet" gap is what the router is seen to carry.
    findContainingFeatures: async (lon: number, lat: number) =>
      storage.features.length > 0
        ? { features: storage.features, gap: "" }
        : actual.findContainingFeatures(lon, lat),
  };
});

import type { Context } from "@/lib/server/trpc/init";
import { landContextRouter } from "@/lib/server/trpc/routers/land-context";

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

const caller = landContextRouter.createCaller({ db: {}, session: null } as unknown as Context);

beforeEach(() => {
  storage.features = [];
});

describe("landContext.resolveBoundaryAtPoint geometry", () => {
  it("attaches the decoded GeoJSON geometry when the storage layer supplies WKB", async () => {
    storage.features = [candidate(POINT_WKB)];
    const response = await caller.resolveBoundaryAtPoint({ lon: -116.2, lat: 43.6 });
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data[0].coverageState).toBe("matched");
    expect(response.data[0].geometry).toEqual({ type: "Point", coordinates: [1, 2] });
    // Geometry crosses the wire as GeoJSON only; the hex is stripped to hold the byte budget.
    expect(response.data[0].sourceFeature?.geometryWkb).toBeNull();
  });

  it("ships geometry: null with the reader's verbatim gap against the placeholder storage layer", async () => {
    const response = await caller.resolveBoundaryAtPoint({ lon: -116.2, lat: 43.6 });
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data).toHaveLength(1);
    expect(response.data[0].coverageState).toBe("unknown_coverage");
    expect(response.data[0].geometry).toBeNull();
    expect(response.data[0].unresolvedGaps).toEqual([
      'no Parquet lane named "land-context-boundaries" appears in the warehouse coverage census; source_unbound_for_region for the land-context reference plane',
    ]);
  });

  it("keeps a matched feature with a null WKB listable, with geometry: null and no invented gap", async () => {
    storage.features = [candidate(null)];
    const response = await caller.resolveBoundaryAtPoint({ lon: -116.2, lat: 43.6 });
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data[0].coverageState).toBe("matched");
    expect(response.data[0].geometry).toBeNull();
    expect(response.data[0].unresolvedGaps).toEqual([]);
  });
});
