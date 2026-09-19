/**
 * Requirement 5: every `LandContextResult` carries the full required field
 * set (source feature/release, overlap basis, org/office, route meaning,
 * assignment evidence, public contact URL, verification time, documented
 * help, unresolved gaps) — even when most are null because no land-context
 * lane is registered in the warehouse, the KEYS must be present with an
 * explicit sentinel, not simply absent from the object.
 *
 * The coverage census is mocked to an EMPTY warehouse, so the readers take
 * their real pointer-GET path to a deterministic "no lane" answer instead of
 * depending on the network.
 */
import { describe, expect, it, vi } from "vitest";

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
import {
  readPointContainment,
  readBoundedAoiIntersection,
  readBoundaryByParcelKey,
  readContactsForSubject,
} from "@/lib/server/services/land-context/reader";
import type { LandContextResult } from "@/lib/server/services/land-context/types";

const REQUIRED_KEYS: (keyof LandContextResult)[] = [
  "coverageState",
  "sourceFeature",
  "sourceRelease",
  "matchedRegionOrOverlap",
  "organizationOffice",
  "route",
  "roleOrRouteType",
  "assignmentEvidence",
  "publicContactUrl",
  "verificationTime",
  "documentedHelp",
  "unresolvedGaps",
  "isCurrentReferenceOnly",
];

function assertFullFieldSet(result: LandContextResult) {
  for (const key of REQUIRED_KEYS) {
    expect(Object.prototype.hasOwnProperty.call(result, key)).toBe(true);
  }
  // Sentinel checks: null/[] are acceptable; `undefined` or absence is not.
  expect(result.sourceFeature).not.toBeUndefined();
  expect(result.sourceRelease).not.toBeUndefined();
  expect(result.matchedRegionOrOverlap).not.toBeUndefined();
  expect(result.organizationOffice).not.toBeUndefined();
  expect(result.route).not.toBeUndefined();
  expect(result.roleOrRouteType).not.toBeUndefined();
  expect(result.assignmentEvidence).not.toBeUndefined();
  expect(result.publicContactUrl).not.toBeUndefined();
  expect(result.verificationTime).not.toBeUndefined();
  expect(result.documentedHelp).not.toBeUndefined();
  expect(Array.isArray(result.unresolvedGaps)).toBe(true);
  expect(typeof result.isCurrentReferenceOnly).toBe("boolean");
}

// Every reader now refuses outright where the deployment's region binds no land-context source
// (STYLE-REVIEW-W8 B1), and no shipped manifest binds one yet, so these read-path assertions are
// about the region that DOES: the gate is pinned open here and proved shut in
// `src/__tests__/region/land-context-second-region.test.ts`.
vi.mock("@/lib/server/services/land-context/region-binding", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/land-context/region-binding")>()),
  isLandContextBoundInRegion: () => true,
}));

describe("LandContextResult carries the full required field set even against the stub source", () => {
  it("readPointContainment result has every required key with explicit sentinels", async () => {
    const result = await readPointContainment(-122.33, 47.6);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.length).toBeGreaterThan(0);
    result.data.forEach(assertFullFieldSet);
    // Unresolved gaps must be non-empty when coverage is unknown.
    expect(result.data[0].unresolvedGaps.length).toBeGreaterThan(0);
  });

  it("readBoundedAoiIntersection result has every required key with explicit sentinels", async () => {
    const bbox = { west: -122.5, south: 47.5, east: -122.0, north: 48.0 };
    const result = await readBoundedAoiIntersection(bbox);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    result.data.forEach(assertFullFieldSet);
  });

  it("readBoundaryByParcelKey result has every required key with explicit sentinels", async () => {
    const result = await readBoundaryByParcelKey({
      sourceNamespace: "id-ada-county-assessor",
      originalId: "0007",
      state: "ID",
    });
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    assertFullFieldSet(result.data);
  });

  it("readContactsForSubject result has every required key with explicit sentinels", async () => {
    const result = await readContactsForSubject("subject-42", "wetlands");
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    result.data.forEach(assertFullFieldSet);
  });
});
