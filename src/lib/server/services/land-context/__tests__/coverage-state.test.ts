/**
 * Requirement 2: coverage states are distinct, never collapsed to
 * null/undefined.
 *
 * `parquet-reader.ts` is currently a stub returning empty-with-gap for every
 * function. Every bounded reader must still surface a typed `CoverageState`
 * for that outcome (e.g. "unknown_coverage"), never a bare null/undefined or
 * an empty array with no accompanying state, per types.ts:
 * "three distinct states the spec forbids collapsing into a bare null."
 */
import { describe, expect, it } from "vitest";
import {
  readPointContainment,
  readBoundedAoiIntersection,
  readBoundaryByParcelKey,
  readContactsForSubject,
  readCoverageForRegion,
} from "@/lib/server/services/land-context/reader";
import type { CoverageState } from "@/lib/server/services/land-context/types";

const VALID_COVERAGE_STATES: CoverageState[] = [
  "matched",
  "no_match_in_proven_coverage",
  "unknown_coverage",
  "unavailable_history",
  "outside_pilot",
  "partial_area_coverage",
];

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
    expect(result.data[0].coverageState).toBe("unknown_coverage");
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
    expect(result.data[0].coverageState).toBe("partial_area_coverage");
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
    expect(result.data.coverageState).toBe("no_match_in_proven_coverage");
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
    expect(result.data[0].coverageState).toBe("unknown_coverage");
  });

  it("readCoverageForRegion returns a typed coverageState (unknown, not null) for a pilot state/county", async () => {
    const result = await readCoverageForRegion("WA", "King");
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.coverageState).not.toBeNull();
    expect(result.data.coverageState).not.toBeUndefined();
    expect(VALID_COVERAGE_STATES).toContain(result.data.coverageState);
    expect(result.data.coverageState).toBe("unknown_coverage");
  });

  it("readCoverageForRegion rejects a non-pilot state as outside_pilot_states, not a silent unknown", async () => {
    const result = await readCoverageForRegion("CA", null);
    expect(result.status).toBe("budget_exceeded");
    if (result.status === "budget_exceeded") {
      expect(result.reason).toBe("outside_pilot_states");
    }
  });
});
