/**
 * The land-context Parquet reads: bounds, ingress validation and governed absence.
 *
 * Network is mocked at `@/lib/server/services/parquet-plane-client` (partially, via
 * `importOriginal`, so `shared.ts` still gets the real error classes it does `instanceof` against).
 * Nothing here asserts that land-context data EXISTS -- no lane is registered today; it asserts
 * that every empty answer arrives as a stated gap and never as a bare empty collection.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ZoomTier } from "@/lib/map/zoom-tiers";

// `vi.hoisted` so the hoisted `vi.mock` factory below may reference these without a TDZ error.
const { getParquetWarehouseCoverage, getParquetLatestRelease } = vi.hoisted(() => ({
  getParquetWarehouseCoverage: vi.fn(),
  getParquetLatestRelease: vi.fn(),
}));

// Partial mock via `importOriginal`: `parquet-trpc-readers/shared.ts` does `instanceof` against
// this module's real error classes, and a whole-module replacement would leave them undefined.
vi.mock("@/lib/server/services/parquet-plane-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/parquet-plane-client")>()),
  getParquetWarehouseCoverage,
  getParquetLatestRelease,
}));

import {
  LAND_CONTEXT_PRODUCT_LAYERS,
  ROUTE_MEANING_VALUES,
  RUNG_MAX_BBOX_SQUARE_DEGREES,
  exactIntersectCandidates,
  findBoundaryByParcelKey,
  findContainingFeatures,
  findRelationshipsAndRoutes,
  pruneCandidatesByBbox,
  readCoverageStatus,
  selectServingRung,
} from "@/lib/server/services/land-context/parquet-reader";
import {
  readBoundaryByParcelKey,
  readBoundedAoiIntersection,
  readContactsForSubject,
  readContactsForSelection,
  readCoverageForRegion,
} from "@/lib/server/services/land-context/reader";
import { UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { readLandContextAvailability } from "../availability";
import { readCropCover, readCropCoverAvailability } from "../crop-cover";

const BOUNDARY_LAYER = LAND_CONTEXT_PRODUCT_LAYERS.boundaries;

/** One census lane, with every mandatory field present so a partial fixture cannot pass by accident. */
function laneCoverage(overrides: {
  layer: string;
  zoomTier: ZoomTier;
  latestDay?: string | null;
  withheldReason?: string | null;
}) {
  const latestDay = overrides.latestDay === undefined ? "2026-09-17" : overrides.latestDay;
  return {
    layer: overrides.layer,
    nature: "release_series",
    kind: "observed",
    zoomTier: overrides.zoomTier,
    earliestDay: latestDay,
    latestDay,
    latestRecordedDay: latestDay,
    publishedRanges: [] as { from: string; to: string }[],
    gapRanges: [],
    governedAbsenceRanges: [],
    coverageAuthority: "availability",
    availabilityGenerationSha256: null,
    availabilityPointerKey: null,
    sourceCeilingDay: null,
    requiredRungs: [0, 5, 9, 13],
    withheldReason: overrides.withheldReason ?? null,
  };
}

function census(lanes: ReturnType<typeof laneCoverage>[]) {
  return {
    coverageSchemaVersion: 1,
    generatedAt: "2026-09-18T00:00:00Z",
    evaluatedThroughDay: "2026-09-18",
    lanes,
  };
}

/** The minimum column set `boundaryRowSchema` declares, as a lane would publish it. */
function boundaryRow(overrides: Record<string, unknown> = {}) {
  return {
    source_namespace: "blm-surface-management-agency",
    native_feature_key: "OR-0042",
    native_feature_version: null,
    family: "blm_surface_management",
    interest_type: "surface_management_agency",
    state: "OR",
    county: "Deschutes",
    geom_wkb: "0101000020E6100000000000000000F03F0000000000000040",
    release_publisher: "Bureau of Land Management",
    release_canonical_endpoint: "https://gbp-blm-egis.hub.arcgis.com/",
    release_source_version: "2026-08",
    release_captured_at: "2026-09-01T00:00:00Z",
    release_source_watermark_at: "2026-08-15T00:00:00Z",
    release_admission_verdict: "admitted",
    ...overrides,
  };
}

function published(rows: unknown[], truncated = false) {
  return {
    state: "published",
    requestedDay: "2026-09-17",
    servedDay: "2026-09-17",
    rows,
    truncated,
  };
}

const SMALL_BBOX = { west: -121.5, south: 44.0, east: -121.0, north: 44.5 }; // 0.25 sq deg
const REGIONAL_BBOX = { west: -125, south: 42, east: -111, north: 49 }; // 98 sq deg

beforeEach(() => {
  getParquetWarehouseCoverage.mockReset();
  getParquetLatestRelease.mockReset();
});

// Every reader now refuses outright where the deployment's region binds no land-context source
// (STYLE-REVIEW-W8 B1), and no shipped manifest binds one yet, so these read-path assertions are
// about the region that DOES: the gate is pinned open here and proved shut in
// `src/__tests__/region/land-context-second-region.test.ts`.
vi.mock("@/lib/server/services/land-context/region-binding", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/land-context/region-binding")>()),
  isLandContextBoundInRegion: () => true,
}));

describe("selectServingRung", () => {
  it("takes the finest published rung whose own ceiling admits the area", () => {
    expect(selectServingRung([13, 9, 5, 0], 0.25)).toEqual({ kind: "selected", rung: 13 });
    expect(selectServingRung([13, 9, 5, 0], 98)).toEqual({ kind: "selected", rung: 9 });
    expect(selectServingRung([13, 9, 5, 0], 500)).toEqual({ kind: "selected", rung: 5 });
  });

  it("steps past a published-but-too-tight rung instead of refusing on it", () => {
    // The 2026-09-14 botanical defect in miniature: 98 sq deg over a 4 sq deg detail rung.
    expect(selectServingRung([13, 5], 98)).toEqual({ kind: "selected", rung: 5 });
  });

  it("names 'no_rung_admits_area', never a guessed rung, when nothing published admits the area", () => {
    expect(selectServingRung([13], 98)).toEqual({ kind: "no_rung_admits_area" });
    expect(selectServingRung([], 0.1)).toEqual({ kind: "no_rung_admits_area" });
  });

  it("bounds the whole ladder", () => {
    expect(RUNG_MAX_BBOX_SQUARE_DEGREES[13]).toBeLessThan(RUNG_MAX_BBOX_SQUARE_DEGREES[9]);
    expect(RUNG_MAX_BBOX_SQUARE_DEGREES[9]).toBeLessThan(RUNG_MAX_BBOX_SQUARE_DEGREES[5]);
    expect(RUNG_MAX_BBOX_SQUARE_DEGREES[5]).toBeLessThan(RUNG_MAX_BBOX_SQUARE_DEGREES[0]);
  });
});

describe("pruneCandidatesByBbox: the pointer GET", () => {
  it("states source_unbound_for_region and reads no data when no land-context lane is in the census", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([laneCoverage({ layer: "watersheds", zoomTier: 13 })])
    );

    const pruned = await pruneCandidatesByBbox(SMALL_BBOX);

    expect(pruned.candidateKeys).toEqual([]);
    // The TYPED member is the assertion; the sentence is a caption. Pinning the prose is what let
    // reader.ts report this as `partial_area_coverage` for a whole wave (STYLE-REVIEW-W2 B3).
    expect(pruned.refusal?.coverageState).toBe("source_unbound_for_region");
    expect(pruned.refusal?.detail).toContain(BOUNDARY_LAYER);
    expect(getParquetLatestRelease).not.toHaveBeenCalled();
  });

  it("names the published partition it proved, so the data GET needs no second census", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([
        laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 13 }),
        laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 5 }),
      ])
    );

    const fine = await pruneCandidatesByBbox(SMALL_BBOX);
    const regional = await pruneCandidatesByBbox(REGIONAL_BBOX);

    expect(fine.candidateKeys).toEqual([
      `${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`,
    ]);
    expect(regional.candidateKeys).toEqual([
      `${BOUNDARY_LAYER}/kind=observed/zoom=05/day=2026-09-17`,
    ]);
    expect(getParquetWarehouseCoverage).toHaveBeenCalledTimes(2);
  });

  it("refuses a bbox wider than every published rung, naming the coarsest rung's ceiling", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 13 })])
    );

    const pruned = await pruneCandidatesByBbox(REGIONAL_BBOX);

    expect(pruned.candidateKeys).toEqual([]);
    expect(pruned.refusal?.coverageState).toBe("unknown_coverage");
    expect(pruned.gap).toContain("exceeds every published rung");
    expect(pruned.gap).toContain("4 square degrees");
  });

  it("reports a withheld availability index in its own words", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([
        laneCoverage({
          layer: BOUNDARY_LAYER,
          zoomTier: 13,
          withheldReason: "availability_checksum_mismatch",
        }),
      ])
    );

    const pruned = await pruneCandidatesByBbox(SMALL_BBOX);

    expect(pruned.candidateKeys).toEqual([]);
    expect(pruned.refusal?.coverageState).toBe("unknown_coverage");
    expect(pruned.gap).toContain("availability_checksum_mismatch");
  });

  it("reports a census transport failure as a transport failure, never as an absence", async () => {
    getParquetWarehouseCoverage.mockRejectedValue(new UpstreamTimeoutError("census timed out"));

    const pruned = await pruneCandidatesByBbox(SMALL_BBOX);

    expect(pruned.candidateKeys).toEqual([]);
    // Distinct from every absence state: a failed read is `upstream_unavailable`, not a finding.
    expect(pruned.refusal?.coverageState).toBe("upstream_unavailable");
    expect(pruned.gap).toContain("timeout");
    expect(pruned.gap).toContain("not a coverage finding");
  });
});

describe("exactIntersectCandidates: the data GET", () => {
  it("uses clipped published geometry and rejects bbox false positives", async () => {
    const geometry = { type: "Polygon", coordinates: [
      [[-121.5, 44], [-121, 44], [-121.5, 44.5], [-121.5, 44]],
    ] };
    getParquetLatestRelease.mockResolvedValue(published([boundaryRow({ geom_wkb: null, geometry_wkb: geometry })]));
    const result = await exactIntersectCandidates(
      [`${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`],
      { west: -121.1, south: 44.4, east: -121.05, north: 44.45 }
    );
    expect(result.features).toHaveLength(0);
  });

  it("withholds unadmitted source releases", async () => {
    getParquetLatestRelease.mockResolvedValue(published([boundaryRow({ release_admission_verdict: "pending" })]));
    const result = await exactIntersectCandidates([`${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`], SMALL_BBOX);
    expect(result.features).toHaveLength(0);
    expect(result.gap).toContain("withheld");
  });
  it("reads nothing at all when the pointer phase pruned everything away", async () => {
    const result = await exactIntersectCandidates([], SMALL_BBOX);

    expect(result.features).toEqual([]);
    expect(result.gap).toContain("no candidate partition");
    expect(getParquetLatestRelease).not.toHaveBeenCalled();
  });

  it("sends the pruned rung, day and bbox verbatim and maps a published row onto the contract", async () => {
    getParquetLatestRelease.mockResolvedValue(published([boundaryRow()]));

    const result = await exactIntersectCandidates(
      [`${BOUNDARY_LAYER}/kind=observed/zoom=09/day=2026-09-17`],
      SMALL_BBOX
    );

    expect(getParquetLatestRelease).toHaveBeenCalledWith({
      layer: BOUNDARY_LAYER,
      asOfDay: "2026-09-17",
      zoomTier: 9,
      kind: "observed",
      bbox: "-121.5,44,-121,44.5",
    });
    expect(result.gap).toBe("");
    expect(result.features).toHaveLength(1);
    const [feature] = result.features;
    expect(feature.boundary.familyType).toBe("blm_surface_management");
    expect(feature.boundary.state).toBe("OR");
    expect(feature.boundary.geometryWkb).toBe(boundaryRow().geom_wkb);
    expect(feature.sourceRelease.sourceVersion).toBe("2026-08");
    // No declared publication-date column, so this stays null rather than echoing the watermark.
    expect(feature.sourceRelease.sourcePublishedTime).toBeNull();
  });

  it("reports a bbox-filtered row as a CANDIDATE overlap, never as proved containment", async () => {
    getParquetLatestRelease.mockResolvedValue(published([boundaryRow()]));

    const result = await exactIntersectCandidates(
      [`${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`],
      SMALL_BBOX
    );

    expect(result.features[0].overlapBasis.kind).toBe("bbox_intersection");
    expect(result.features[0].overlapBasis.description).toContain("containment is not proved");
  });

  it("refuses rows that do not match the declared schema, rather than admitting a partial feature", async () => {
    getParquetLatestRelease.mockResolvedValue(
      published([boundaryRow({ state: "CA" }), boundaryRow()])
    );

    const result = await exactIntersectCandidates(
      [`${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`],
      SMALL_BBOX
    );

    expect(result.features).toEqual([]);
    expect(result.gap).toContain("do not match the schema");
  });

  it("surfaces a truncated answer as a gap instead of a silent subset", async () => {
    getParquetLatestRelease.mockResolvedValue(published([boundaryRow()], true));

    const result = await exactIntersectCandidates(
      [`${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`],
      SMALL_BBOX
    );

    expect(result.features).toHaveLength(1);
    expect(result.gap).toContain("serving row budget");
  });

  it("quotes a governed absence's own evidence", async () => {
    getParquetLatestRelease.mockResolvedValue({
      state: "governed_absence",
      requestedDay: "2026-09-17",
      servedDay: "2026-09-17",
      evidence: {
        reason: "source_unbound_for_region",
        upstreamResponse: "no BLM extract admitted for OR",
        recordedAt: "2026-09-17T06:00:00Z",
        runId: "run-1",
      },
    });

    const result = await exactIntersectCandidates(
      [`${BOUNDARY_LAYER}/kind=observed/zoom=13/day=2026-09-17`],
      SMALL_BBOX
    );

    expect(result.features).toEqual([]);
    expect(result.gap).toContain("governed absence");
    expect(result.gap).toContain("no BLM extract admitted for OR");
  });

  it("rejects a candidate key it did not issue", async () => {
    const result = await exactIntersectCandidates(["not-a-partition-key"], SMALL_BBOX);

    expect(result.features).toEqual([]);
    expect(result.gap).toContain("not a partition key this reader issued");
    expect(getParquetLatestRelease).not.toHaveBeenCalled();
  });
});

describe("findContainingFeatures", () => {
  it("probes the point as a small square, because a degenerate rectangle is not a readable bbox", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 13 })])
    );
    getParquetLatestRelease.mockResolvedValue(published([]));

    await findContainingFeatures(-121.25, 44.25);

    const sent = getParquetLatestRelease.mock.calls[0][0] as { bbox: string };
    const [west, south, east, north] = sent.bbox.split(",").map(Number);
    expect(west).toBeLessThan(east);
    expect(south).toBeLessThan(north);
    expect(east - west).toBeCloseTo(0.0002, 6);
  });
});

describe("the reads that stop short, honestly", () => {
  it("findBoundaryByParcelKey names the missing capability rather than returning a fabricated miss", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 13 })])
    );

    const result = await findBoundaryByParcelKey({
      sourceNamespace: "wa-king-county-assessor",
      originalId: "0001234567",
      state: "WA",
    });

    expect(result.feature).toBeNull();
    expect(result.gap).toContain("no key-addressed read");
    expect(getParquetLatestRelease).not.toHaveBeenCalled();
  });

  it("readCoverageStatus stays unknown even when the lane publishes, and says why", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(
      census([laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 0 })])
    );

    const result = await readCoverageStatus("WA", "King");

    expect(result.covered).toBeNull();
    expect(result.gap).toContain("no per-region coverage product");
    expect(result.gap).toContain("King, WA");
    // The pointer RESOLVED, so there is no refusal to forward: this is a genuine unknown, and
    // `reader.ts` is free to answer `unknown_coverage` for it.
    expect(result.refusal).toBeNull();
  });

  it("findBoundaryByParcelKey carries the pointer's typed refusal, not only its prose", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([]));

    const result = await findBoundaryByParcelKey({
      sourceNamespace: "wa-king-county-assessor",
      originalId: "0001234567",
      state: "WA",
    });

    expect(result.refusal?.coverageState).toBe("source_unbound_for_region");
  });

  it("findRelationshipsAndRoutes carries the pointer's typed refusal, not only its prose", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([]));

    const result = await findRelationshipsAndRoutes("some-subject-id", null);

    expect(result.relationships).toEqual([]);
    expect(result.refusal?.coverageState).toBe("source_unbound_for_region");
  });

  it("readCoverageStatus carries the pointer's typed refusal, not only its prose", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([]));

    const result = await readCoverageStatus("WA", "King");

    expect(result.refusal?.coverageState).toBe("source_unbound_for_region");
  });
});

/**
 * The three readers STYLE-REVIEW-W4 B3 found inventing a coverage state. Each one answered an
 * unbound source and a failed census with the same member, so a transport failure was reported as a
 * coverage finding -- the collapse `land-context/AGENTS.md` says never happens.
 */
describe("the three readers that used to invent a coverage state", () => {
  it("readBoundaryByParcelKey reports a census timeout as upstream_unavailable", async () => {
    getParquetWarehouseCoverage.mockRejectedValue(new UpstreamTimeoutError("census timed out"));

    const response = await readBoundaryByParcelKey({
      sourceNamespace: "wa-king-county-assessor",
      originalId: "0001234567",
      state: "WA",
    });

    expect(response.status).toBe("ok");
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data.coverageState).toBe("upstream_unavailable");
  });

  it("readContactsForSubject reports a census timeout as upstream_unavailable", async () => {
    getParquetWarehouseCoverage.mockRejectedValue(new UpstreamTimeoutError("census timed out"));

    const response = await readContactsForSubject("some-subject-id", null);

    expect(response.status).toBe("ok");
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data[0].coverageState).toBe("upstream_unavailable");
  });

  it("readCoverageForRegion reports a census timeout as upstream_unavailable", async () => {
    getParquetWarehouseCoverage.mockRejectedValue(new UpstreamTimeoutError("census timed out"));

    const response = await readCoverageForRegion("WA", "King");

    expect(response.status).toBe("ok");
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data.coverageState).toBe("upstream_unavailable");
    expect(response.data.gap).toContain("not a coverage finding");
  });

  it("each of the three reports an unregistered lane as source_unbound_for_region", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([]));

    const parcel = await readBoundaryByParcelKey({
      sourceNamespace: "wa-king-county-assessor",
      originalId: "0001234567",
      state: "WA",
    });
    const contacts = await readContactsForSubject("some-subject-id", null);
    const region = await readCoverageForRegion("WA", "King");

    if (parcel.status !== "ok" || contacts.status !== "ok" || region.status !== "ok") {
      throw new Error("expected ok");
    }
    expect(parcel.data.coverageState).toBe("source_unbound_for_region");
    expect(contacts.data[0].coverageState).toBe("source_unbound_for_region");
    expect(region.data.coverageState).toBe("source_unbound_for_region");
  });
});

describe("the bounded reader above it", () => {
  it("retains data-phase failures as failures and partial rows as partial", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([laneCoverage({ layer: BOUNDARY_LAYER, zoomTier: 13 })]));
    getParquetLatestRelease.mockRejectedValueOnce(new UpstreamTimeoutError("data timed out"));
    const failed = await readBoundedAoiIntersection(SMALL_BBOX);
    expect(failed).toMatchObject({ status: "ok", data: [{ coverageState: "upstream_unavailable" }] });
    getParquetLatestRelease.mockResolvedValueOnce(published([boundaryRow()], true));
    const partial = await readBoundedAoiIntersection(SMALL_BBOX);
    expect(partial.status).toBe("ok");
    if (partial.status !== "ok") throw new Error("expected bounded response");
    expect(partial.data.map((row) => row.coverageState)).toEqual(["matched", "partial_area_coverage"]);
    expect(partial.data[0].unresolvedGaps[0]).toContain("row budget");
  });
  it("turns an unregistered lane into a typed coverage state carrying the census's own words", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([]));

    const response = await readBoundedAoiIntersection(SMALL_BBOX);

    expect(response.status).toBe("ok");
    if (response.status !== "ok") throw new Error("expected ok");
    expect(response.data).toHaveLength(1);
    // The typed state, not the prose: an unregistered lane is an UNBOUND source, never the
    // positive claim `partial_area_coverage` makes (STYLE-REVIEW-W2 B3).
    expect(response.data[0].coverageState).toBe("source_unbound_for_region");
    expect(response.data[0].sourceFeature).toBeNull();
    // Never an empty collection with nothing said: the census's own words travel out to the UI.
    expect(response.data[0].unresolvedGaps[0]).toContain(BOUNDARY_LAYER);
  });
});

describe("contract ties", () => {
  it("declares exactly the five route meanings the frozen contract names", () => {
    expect(ROUTE_MEANING_VALUES).toEqual([
      "records_assistance",
      "responsible_agency_program",
      "advisory_sme",
      "contact_process_inquiry",
      "documented_introduction_forwarding",
    ]);
  });
});


describe("family availability and annual crop evidence", () => {
  it("joins exact office jurisdictions to routes once, without treating surface IDs as offices", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census([
      laneCoverage({ layer: LAND_CONTEXT_PRODUCT_LAYERS.offices, zoomTier: 13 }),
      laneCoverage({ layer: LAND_CONTEXT_PRODUCT_LAYERS.contacts, zoomTier: 0 }),
    ]));
    const office = boundaryRow({ source_namespace: "blm-field-offices", native_feature_key: "OR1",
      family: "blm_office_jurisdiction", geom_wkb: null, geometry_wkb: { type: "Polygon", coordinates: [
        [[-121.5, 44], [-121, 44], [-121, 44.5], [-121.5, 44.5], [-121.5, 44]],
      ] } });
    const contact = {
      subject_id: "blm-field-offices:OR1", object_id: "office-1", relationship_kind: "geographic_jurisdiction_overlap",
      applicable_geography: "Published office boundary", documented_topic: null,
      assignment_method: "source_documented_office", review_status: "reviewed", effective_from: null, effective_to: null,
      source_evidence_url: "https://www.blm.gov/", organization_id: "blm", office_id: "office-1",
      official_public_name: "Public Office", office_type: "field_office", parent_organization_id: null,
      route_type: "official_state_website", route_meaning: "records_assistance", documented_help: "State website for inquiry",
      official_inquiry_url: "https://www.blm.gov/oregon-washington", public_business_phone: null,
      public_business_email: null, published_professional_name: null, route_status: "active", verified_at: null,
      forwarding_documented: false,
    };
    getParquetLatestRelease.mockImplementation(async ({ layer }: { layer: string }) => published(
      layer === LAND_CONTEXT_PRODUCT_LAYERS.offices ? [office] : [contact]
    ));
    const result = await readContactsForSelection({ mode: "point", lon: -121.25, lat: 44.25 });
    expect(result).toMatchObject({ status: "ok", data: [{ organizationOffice: { officeId: "office-1" },
      matchedRegionOrOverlap: { kind: "point_containment" } }] });
    expect(getParquetLatestRelease.mock.calls.filter(([input]) => input.layer === LAND_CONTEXT_PRODUCT_LAYERS.contacts)).toHaveLength(1);
  });
  it("enables only published BLM, never the other administrative families", async () => {
    getParquetWarehouseCoverage.mockResolvedValue(census(([0, 5, 9, 13] as const).map((zoomTier) => laneCoverage({ layer: BOUNDARY_LAYER, zoomTier }))));
    const groups = await readLandContextAvailability();
    expect(groups.filter((group) => group.status === "available").map((group) => group.group)).toEqual(["blm-lands"]);
    getParquetWarehouseCoverage.mockResolvedValue(census([]));
    expect((await readLandContextAvailability()).find((group) => group.group === "blm-lands")?.status).toBe("not_published");
  });

  it("withholds incomplete editions and uses regional crop grid resolution", async () => {
    const ladder = ([0, 5, 9, 13] as const).map((zoomTier) => laneCoverage({ layer: "crop-cover", zoomTier, latestDay: "2026-02-27" }));
    getParquetWarehouseCoverage.mockResolvedValue(census(ladder.slice(0, 3)));
    expect(await readCropCoverAvailability()).toMatchObject({ available: false, releaseDays: [] });
    getParquetWarehouseCoverage.mockResolvedValue(census(ladder.map((lane) => lane.zoomTier === 13 ?
      { ...lane, latestDay: "2025-02-27", latestRecordedDay: "2025-02-27" } : lane)));
    expect(await readCropCoverAvailability()).toMatchObject({ available: false, releaseDays: [] });
    getParquetWarehouseCoverage.mockResolvedValue(census(ladder));
    getParquetLatestRelease.mockResolvedValue(published([]));
    await readCropCover(REGIONAL_BBOX, "2026-02-27", 13);
    expect(getParquetLatestRelease).toHaveBeenLastCalledWith(expect.objectContaining({ zoomTier: 5 }));
    await readCropCover({ west: -123, south: 43, east: -121, north: 45 }, "2026-02-27", 13);
    expect(getParquetLatestRelease).toHaveBeenLastCalledWith(expect.objectContaining({ zoomTier: 9 }));
  });

  it("honors the requested zoom ceiling on small regional views", () => {
    expect(selectServingRung([0, 5, 9, 13], 0.25, 5)).toEqual({ kind: "selected", rung: 5 });
  });

  it("offers only published annual editions and preserves classification provenance", async () => {
    const lane = { ...laneCoverage({ layer: "crop-cover", zoomTier: 5, latestDay: "2026-02-27" }),
      publishedRanges: [{ from: "2025-02-27", to: "2025-02-27" }, { from: "2026-02-27", to: "2026-02-27" }] };
    getParquetWarehouseCoverage.mockResolvedValue(census(([0, 5, 9, 13] as const).map((zoomTier) => ({ ...lane, zoomTier }))));
    expect((await readCropCoverAvailability()).releaseDays).toEqual(["2026-02-27", "2025-02-27"]);
    const row = {
      feature_id: "crop-cell-1", observed_year: 2024, release_day: "2025-02-27", source: "usda-cdl",
      source_url: "https://nassgeodata.gmu.edu/", source_resolution_m: 10, analysis_resolution_m: 30,
      aggregation_cell_m: 1000, estimation_method: "nearest_neighbor_resampled_pixel_area",
      dominant_crop_code: 1, dominant_crop_name: "Corn", crop_fraction: 0.4, classified_fraction: 0.8,
      crop_area_ha: 40, cell_area_ha: 100, class_areas_json: '{"1":40,"141":40}',
      geometry_wkb: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] },
      source_sha256: "a".repeat(64), ingested_at: "2026-09-20T00:00:00Z",
    };
    getParquetLatestRelease.mockResolvedValue({ ...published([row]), servedDay: "2025-02-27" });
    const result = await readCropCover(SMALL_BBOX, "2025-02-27", 5);
    expect(getParquetLatestRelease).toHaveBeenCalledWith(expect.objectContaining({ asOfDay: "2025-02-27", zoomTier: 5 }));
    expect(result.geojson?.features[0].properties).toMatchObject({ observed_year: 2024, crop_fraction: 0.4, source_sha256: "a".repeat(64) });
    expect(result.message).toContain("not confidence scores");
    getParquetLatestRelease.mockResolvedValue(published([{ ...row, release_day: "2026-02-27" }]));
    await expect(readCropCover(SMALL_BBOX, "2025-02-27", 5)).rejects.toThrow("newer than the requested day");
  });
});
