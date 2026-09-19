/**
 * Requirement 2: coverage states are distinct, never collapsed to
 * null/undefined.
 *
 * `parquet-reader.ts` now issues a real pointer GET against the warehouse
 * coverage census, and no land-context lane is registered, so every read
 * resolves to empty-with-gap-stated. Every bounded reader must still surface a
 * typed `CoverageState` for that outcome ("source_unbound_for_region" when no lane is bound), never a
 * bare null/undefined or an empty array with no accompanying state, per
 * types.ts: "three distinct states the spec forbids collapsing into a bare
 * null."
 *
 * The census is mocked to an EMPTY warehouse so this file asserts coverage
 * typing against a deterministic "no land-context lane" answer rather than
 * against whatever the network happens to do.
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
  readCoverageForRegion,
} from "@/lib/server/services/land-context/reader";
import { COVERAGE_STATES } from "@/lib/server/services/land-context/types";

// Derived, never restated: a new member must be admitted here by the contract, not by this list.
const VALID_COVERAGE_STATES: readonly string[] = COVERAGE_STATES;

// Every reader now refuses outright where the deployment's region binds no land-context source
// (STYLE-REVIEW-W8 B1), and no shipped manifest binds one yet, so these read-path assertions are
// about the region that DOES: the gate is pinned open here and proved shut in
// `src/__tests__/region/land-context-second-region.test.ts`.
vi.mock("@/lib/server/services/land-context/region-binding", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/land-context/region-binding")>()),
  isLandContextBoundInRegion: () => true,
}));

describe("coverage state typing against the stub parquet-reader", () => {
  it("readPointContainment returns a typed coverageState, never null/undefined, for an unresolved point", async () => {
    const result = await readPointContainment(-122.33, 47.6);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.length).toBeGreaterThan(0);
    for (const entry of result.data) {
      expect(entry.coverageState).toBeDefined();
      expect(entry.coverageState).not.toBeNull();
      expect(VALID_COVERAGE_STATES).toContain(entry.coverageState);
    }
    // The census is mocked EMPTY, so no land-context lane is bound here at all: the governed
    // absence, not the weaker "coverage unknown" (STYLE-REVIEW-W2 B3).
    expect(result.data[0].coverageState).toBe("source_unbound_for_region");
  });

  it("readBoundedAoiIntersection returns a typed coverageState for a within-budget AOI with no matches", async () => {
    const bbox = { west: -122.5, south: 47.5, east: -122.0, north: 48.0 };
    const result = await readBoundedAoiIntersection(bbox);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    for (const entry of result.data) {
      expect(entry.coverageState).not.toBeNull();
      expect(entry.coverageState).not.toBeUndefined();
      expect(VALID_COVERAGE_STATES).toContain(entry.coverageState);
    }
    expect(result.data[0].coverageState).toBe("source_unbound_for_region");
  });

  it("readBoundaryByParcelKey returns a typed coverageState for a pilot-state key with no match", async () => {
    const result = await readBoundaryByParcelKey({
      sourceNamespace: "wa-king-county-assessor",
      originalId: "0001234567",
      state: "WA",
    });
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.coverageState).not.toBeNull();
    expect(result.data.coverageState).not.toBeUndefined();
    expect(VALID_COVERAGE_STATES).toContain(result.data.coverageState);
    // An unregistered lane is an UNBOUND source. `no_match_in_proven_coverage` -- what this reader
    // answered until 2026-09-18 -- asserts the area IS proven-covered and this parcel is not in it,
    // which is a stronger positive claim than the state W2 B3 already blocked (W4 B3).
    expect(result.data.coverageState).toBe("source_unbound_for_region");
  });

  it("readContactsForSubject returns a typed coverageState when no relationships are found", async () => {
    const result = await readContactsForSubject("some-subject-id", null);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    for (const entry of result.data) {
      expect(entry.coverageState).not.toBeNull();
      expect(entry.coverageState).not.toBeUndefined();
      expect(VALID_COVERAGE_STATES).toContain(entry.coverageState);
    }
    expect(result.data[0].coverageState).toBe("source_unbound_for_region");
  });

  it("readCoverageForRegion returns a typed coverageState (unknown, not null) for a pilot state/county", async () => {
    const result = await readCoverageForRegion("WA", "King");
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.coverageState).not.toBeNull();
    expect(result.data.coverageState).not.toBeUndefined();
    expect(VALID_COVERAGE_STATES).toContain(result.data.coverageState);
    expect(result.data.coverageState).toBe("source_unbound_for_region");
  });

  it("readCoverageForRegion rejects a non-pilot state as outside_pilot_states, not a silent unknown", async () => {
    const result = await readCoverageForRegion("CA", null);
    expect(result.status).toBe("budget_exceeded");
    if (result.status === "budget_exceeded") {
      expect(result.reason).toBe("outside_pilot_states");
    }
  });
});
