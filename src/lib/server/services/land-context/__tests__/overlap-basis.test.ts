/**
 * Requirement 3: overlap basis is never "centroid" or "nearest".
 *
 * The spec's hard constraint (reader.ts module doc): "never reduce a
 * selected area to its centroid or the nearest office, and always return
 * every intersecting feature/office found." `OverlapBasis.kind` must only
 * ever be one of the three documented values. Since the stub parquet-reader
 * never returns matched features today, this file uses `vi.mock` to inject
 * synthetic candidates through `readPointContainment` and
 * `readBoundedAoiIntersection` and asserts the kind on the resulting
 * `matchedRegionOrOverlap`.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { OverlapBasis } from "@/lib/server/services/land-context/types";

const VALID_OVERLAP_KINDS: OverlapBasis["kind"][] = [
  "point_containment",
  "bbox_intersection",
  "exact_geometry_intersection",
];

vi.mock("@/lib/server/services/land-context/parquet-reader", async () => {
  const actual = await vi.importActual<typeof import("@/lib/server/services/land-context/parquet-reader")>(
    "@/lib/server/services/land-context/parquet-reader"
  );

  const sourceRelease = {
    publisher: "Test Publisher",
    canonicalEndpoint: "https://example.test/dataset",
    sourceVersion: "v1",
    captureTime: "2026-09-01T00:00:00Z",
    sourceEffectiveTime: "2026-09-01T00:00:00Z",
    sourcePublishedTime: "2026-09-01T00:00:00Z",
    admissionVerdict: "admitted" as const,
  };

  const boundary = {
    sourceNamespace: "wa-king-county-assessor",
    nativeFeatureKey: "parcel-123",
    nativeFeatureVersion: "1",
    familyType: "parcel",
    interestType: "fee",
    state: "WA" as const,
    county: "King",
  };

  return {
    ...actual,
    findContainingFeatures: vi.fn(async () => ({
      features: [
        {
          boundary,
          overlapBasis: { kind: "point_containment" as const, description: "point lies within polygon" },
          sourceRelease,
        },
      ],
      gap: "",
      refusal: null,
    })),
    pruneCandidatesByBbox: vi.fn(async () => ({
      candidateKeys: ["parcel-123", "parcel-456"],
      gap: "",
      refusal: null,
    })),
    exactIntersectCandidates: vi.fn(async () => ({
      features: [
        {
          boundary,
          overlapBasis: { kind: "bbox_intersection" as const, description: "bbox overlaps candidate row group" },
          sourceRelease,
        },
        {
          boundary: { ...boundary, nativeFeatureKey: "parcel-456" },
          overlapBasis: {
            kind: "exact_geometry_intersection" as const,
            description: "polygon geometries intersect exactly",
          },
          sourceRelease,
        },
      ],
      gap: "",
    })),
  };
});

// Every reader now refuses outright where the deployment's region binds no land-context source
// (STYLE-REVIEW-W8 B1), and no shipped manifest binds one yet, so these read-path assertions are
// about the region that DOES: the gate is pinned open here and proved shut in
// `src/__tests__/region/land-context-second-region.test.ts`.
vi.mock("@/lib/server/services/land-context/region-binding", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/land-context/region-binding")>()),
  isLandContextBoundInRegion: () => true,
}));

describe("OverlapBasis.kind never collapses to centroid/nearest", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("readPointContainment surfaces only the documented point_containment kind", async () => {
    const { readPointContainment } = await import("@/lib/server/services/land-context/reader");
    const result = await readPointContainment(-122.33, 47.6);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    for (const entry of result.data) {
      expect(entry.matchedRegionOrOverlap).not.toBeNull();
      const kind = entry.matchedRegionOrOverlap?.kind;
      expect(VALID_OVERLAP_KINDS).toContain(kind);
      expect(kind).not.toBe("centroid");
      expect(kind).not.toBe("nearest");
    }
  });

  it("readBoundedAoiIntersection surfaces bbox_intersection / exact_geometry_intersection for every returned feature, and returns ALL candidates, not one", async () => {
    const { readBoundedAoiIntersection } = await import("@/lib/server/services/land-context/reader");
    const bbox = { west: -122.5, south: 47.5, east: -122.0, north: 48.0 };
    const result = await readBoundedAoiIntersection(bbox);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.length).toBe(2);
    for (const entry of result.data) {
      const kind = entry.matchedRegionOrOverlap?.kind;
      expect(VALID_OVERLAP_KINDS).toContain(kind);
      expect(kind).not.toBe("centroid");
      expect(kind).not.toBe("nearest");
    }
  });
});
