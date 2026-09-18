/**
 * Requirement 6: readers never return a single "best guess" result when the
 * (stubbed) source implies multiple candidates could exist. This module's
 * function names for point/area resolution are `readPointContainment` /
 * `readBoundedAoiIntersection`, and for contact lookup `readContactsForSubject`
 * (no separate `resolveBoundaryAtPoint`/`resolveBoundaryInArea`/
 * `lookupContactsForSubject` exports exist in this codebase — these are the
 * reader.ts equivalents per reader.ts's own doc comments: "Returns every
 * containing feature, never just the nearest or a single winning one" and
 * "Returns every applicable office/route, not a single best pick").
 *
 * `parquet-reader.ts` is mocked in-file only (not edited) to return 2+
 * synthetic candidates, exercising the "return all candidates" path the
 * always-empty real stub can never reach.
 */
import { describe, expect, it, vi } from "vitest";

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

  const boundaryA = {
    sourceNamespace: "wa-king-county-assessor",
    nativeFeatureKey: "parcel-A",
    nativeFeatureVersion: "1",
    familyType: "parcel",
    interestType: "fee",
    state: "WA" as const,
    county: "King",
  };
  const boundaryB = {
    ...boundaryA,
    sourceNamespace: "wa-king-county-critical-areas",
    nativeFeatureKey: "critical-area-B",
    familyType: "overlay",
  };

  return {
    ...actual,
    findContainingFeatures: vi.fn(async () => ({
      features: [
        {
          boundary: boundaryA,
          overlapBasis: { kind: "point_containment" as const, description: "point lies within parcel A" },
          sourceRelease,
        },
        {
          boundary: boundaryB,
          overlapBasis: { kind: "point_containment" as const, description: "point lies within overlapping critical area B" },
          sourceRelease,
        },
      ],
      gap: "",
      refusal: null,
    })),
    pruneCandidatesByBbox: vi.fn(async () => ({
      candidateKeys: ["parcel-A", "critical-area-B"],
      gap: "",
      refusal: null,
    })),
    exactIntersectCandidates: vi.fn(async () => ({
      features: [
        {
          boundary: boundaryA,
          overlapBasis: { kind: "bbox_intersection" as const, description: "bbox overlaps parcel A" },
          sourceRelease,
        },
        {
          boundary: boundaryB,
          overlapBasis: { kind: "bbox_intersection" as const, description: "bbox overlaps critical area B" },
          sourceRelease,
        },
      ],
      gap: "",
    })),
    findRelationshipsAndRoutes: vi.fn(async () => ({
      relationships: [
        {
          subjectId: "subject-42",
          objectId: "office-1",
          relationshipKind: "records_assistance",
          applicableGeography: "King County",
          documentedTopic: "wetlands",
          assignmentMethod: "manual-crosswalk",
          reviewStatus: "reviewed" as const,
          effectiveFrom: "2026-01-01",
          effectiveTo: null,
          sourceEvidenceUrl: "https://example.test/evidence-1",
        },
        {
          subjectId: "subject-42",
          objectId: "office-2",
          relationshipKind: "advisory_sme",
          applicableGeography: "King County",
          documentedTopic: "wetlands",
          assignmentMethod: "manual-crosswalk",
          reviewStatus: "unreviewed" as const,
          effectiveFrom: "2026-01-01",
          effectiveTo: null,
          sourceEvidenceUrl: "https://example.test/evidence-2",
        },
      ],
      offices: [
        {
          organizationId: "org-1",
          officeId: "office-1",
          officialPublicName: "King County Wetlands Program",
          officeType: "program",
          parentOrganizationId: null,
        },
        {
          organizationId: "org-2",
          officeId: "office-2",
          officialPublicName: "WA Dept of Ecology Wetlands Advisor",
          officeType: "advisory",
          parentOrganizationId: null,
        },
      ],
      routes: [
        {
          officeId: "office-1",
          routeType: "web-form",
          routeMeaning: "records_assistance" as const,
          documentedTopic: "wetlands",
          documentedHelp: "General wetlands records requests.",
          officialInquiryUrl: "https://example.test/office-1",
          publicPhone: null,
          publicEmail: null,
          optionalProfessionalName: null,
          status: "active" as const,
          verificationTime: "2026-09-01T00:00:00Z",
          supportsIntroductionOrForwarding: false,
        },
        {
          officeId: "office-2",
          routeType: "email",
          routeMeaning: "advisory_sme" as const,
          documentedTopic: "wetlands",
          documentedHelp: "Technical wetlands guidance.",
          officialInquiryUrl: "https://example.test/office-2",
          publicPhone: null,
          publicEmail: null,
          optionalProfessionalName: null,
          status: "active" as const,
          verificationTime: "2026-09-01T00:00:00Z",
          supportsIntroductionOrForwarding: false,
        },
      ],
      gap: "",
      refusal: null,
    })),
  };
});

describe("readers return every candidate, never a single best guess", () => {
  it("readPointContainment returns both overlapping features for a point, not just one", async () => {
    const { readPointContainment } = await import("@/lib/server/services/land-context/reader");
    const result = await readPointContainment(-122.33, 47.6);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.length).toBe(2);
    const featureKeys = result.data.map((r) => r.sourceFeature?.nativeFeatureKey);
    expect(featureKeys).toEqual(expect.arrayContaining(["parcel-A", "critical-area-B"]));
  });

  it("readBoundedAoiIntersection returns both intersecting features for an AOI, not just one", async () => {
    const { readBoundedAoiIntersection } = await import("@/lib/server/services/land-context/reader");
    const bbox = { west: -122.5, south: 47.5, east: -122.0, north: 48.0 };
    const result = await readBoundedAoiIntersection(bbox);
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.length).toBe(2);
  });

  it("readContactsForSubject returns every applicable office/route, not a single best pick, and preserves review-status distinctions", async () => {
    const { readContactsForSubject } = await import("@/lib/server/services/land-context/reader");
    const result = await readContactsForSubject("subject-42", "wetlands");
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.data.length).toBe(2);

    const officeNames = result.data.map((r) => r.organizationOffice?.officialPublicName);
    expect(officeNames).toEqual(
      expect.arrayContaining(["King County Wetlands Program", "WA Dept of Ecology Wetlands Advisor"])
    );

    const reviewed = result.data.find((r) => r.organizationOffice?.officeId === "office-1");
    const unreviewed = result.data.find((r) => r.organizationOffice?.officeId === "office-2");
    expect(reviewed?.unresolvedGaps).not.toContain(
      "assignment method is not reviewed; treat as unverified office assignment"
    );
    expect(unreviewed?.unresolvedGaps).toContain(
      "assignment method is not reviewed; treat as unverified office assignment"
    );
  });
});
