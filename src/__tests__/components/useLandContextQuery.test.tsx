import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LandContextResult } from "@/lib/environmental/land-context-contract";
import type { LandContextGroupId } from "@/stores/land-context-store";

/**
 * The map-facing adapter over the `landContext` boundary procedures: a decoded geometry from
 * the router is drawn as-is, a null one keeps the feature listable without inventing a shape,
 * and a non-matched coverage result is carried out as a typed notice instead of being dropped.
 */
interface QueryState {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

const queries = vi.hoisted(() => ({
  point: { data: undefined, isLoading: false, isError: false, error: null } as QueryState,
  area: { data: undefined, isLoading: false, isError: false, error: null } as QueryState,
  pointUseQuery: vi.fn(),
  areaUseQuery: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    landContext: {
      resolveBoundaryAtPoint: {
        useQuery: (input: unknown, options: unknown) => {
          queries.pointUseQuery(input, options);
          return queries.point;
        },
      },
      resolveBoundaryInArea: {
        useQuery: (input: unknown, options: unknown) => {
          queries.areaUseQuery(input, options);
          return queries.area;
        },
      },
    },
  },
}));

import { useLandContextQuery } from "@/components/map/land-context/useLandContextQuery";

const ALL_OFF: Record<LandContextGroupId, boolean> = {
  "parcels-land-use": false,
  "electric-utility-territories": false,
  "blm-lands": false,
  "state-managed-lands": false,
};
const BLM_ON = { ...ALL_OFF, "blm-lands": true };
const POINT = { mode: "point" as const, point: [-116.2, 43.6] as [number, number] };
const GAP = "no Parquet lane wired in yet; reference plane not yet admitted for reads";

const POLYGON: GeoJSON.Polygon = {
  type: "Polygon",
  coordinates: [
    [
      [-116.3, 43.5],
      [-116.1, 43.5],
      [-116.1, 43.7],
      [-116.3, 43.5],
    ],
  ],
};

type BoundaryResult = LandContextResult & { geometry: GeoJSON.Geometry | null };

function matched(geometry: GeoJSON.Geometry | null, familyType = "blm_surface_management"): BoundaryResult {
  return {
    coverageState: "matched",
    sourceFeature: {
      sourceNamespace: "blm-national-sma",
      nativeFeatureKey: "SMA-000123",
      nativeFeatureVersion: null,
      familyType,
      interestType: "surface_management",
      state: "ID",
      county: "Ada",
      geometryWkb: geometry ? "0103..." : null,
    },
    sourceRelease: {
      publisher: "Bureau of Land Management",
      canonicalEndpoint: "https://gis.blm.gov/",
      sourceVersion: "2026-09",
      captureTime: null,
      sourceEffectiveTime: null,
      sourcePublishedTime: null,
      admissionVerdict: "admitted",
    },
    matchedRegionOrOverlap: { kind: "point_containment", description: "point inside polygon" },
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: [],
    isCurrentReferenceOnly: true,
    geometry,
  };
}

function coverageOnly(coverageState: LandContextResult["coverageState"], gaps: string[]): BoundaryResult {
  return {
    coverageState,
    sourceFeature: null,
    sourceRelease: null,
    matchedRegionOrOverlap: null,
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: gaps,
    isCurrentReferenceOnly: true,
    geometry: null,
  };
}

beforeEach(() => {
  queries.point = { data: undefined, isLoading: false, isError: false, error: null };
  queries.area = { data: undefined, isLoading: false, isError: false, error: null };
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useLandContextQuery geometry", () => {
  it("keeps a stable source identity and provenance when result ordering changes", () => {
    const boundary = matched(POLYGON);
    queries.point.data = { status: "ok", data: [boundary] };
    const { result, rerender } = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    const id = result.current.data[0].id;
    queries.point.data = { status: "ok", data: [coverageOnly("unknown_coverage", [GAP]), boundary] };
    rerender();
    expect(result.current.data[0].id).toBe(id);
    expect(result.current.data[0].evidence?.sourceRelease).toEqual(boundary.sourceRelease);
  });
  it("draws the geometry the router decoded", () => {
    queries.point.data = { status: "ok", data: [matched(POLYGON)] };
    const { result } = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(result.current.status).toBe("settled");
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data[0].group).toBe("blm-lands");
    expect(result.current.data[0].geometry).toEqual(POLYGON);
  });

  it("keeps a feature with no geometry listable via an EMPTY GeometryCollection, never a shape", () => {
    queries.point.data = { status: "ok", data: [matched(null)] };
    const { result } = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data[0].geometry).toEqual({ type: "GeometryCollection", geometries: [] });
    expect(result.current.data[0].title).toBe("SMA-000123");
  });
});

describe("useLandContextQuery coverage and status", () => {
  it("carries a non-matched coverage result out as a typed notice with its verbatim gaps, not as a feature", () => {
    queries.point.data = { status: "ok", data: [coverageOnly("unknown_coverage", [GAP])] };
    const { result } = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(result.current.data).toEqual([]);
    expect(result.current.status).toBe("settled");
    expect(result.current.meta).toMatchObject({
      totalCount: 0,
      returnedCount: 0,
      coverageNotices: [{ coverageState: "unknown_coverage", gaps: [GAP] }],
    });
  });

  it("counts only matched results in totalCount and only enabled groups in returnedCount", () => {
    queries.point.data = {
      status: "ok",
      data: [matched(POLYGON), matched(POLYGON, "parcel"), coverageOnly("unknown_coverage", [GAP])],
    };
    const { result } = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(result.current.data.map((feature) => feature.group)).toEqual(["blm-lands"]);
    expect(result.current.meta).toMatchObject({ totalCount: 2, returnedCount: 1 });
  });

  it("is idle with no selection or no group, and does not enable the query", () => {
    const noSelection = renderHook(() => useLandContextQuery(null, BLM_ON));
    expect(noSelection.result.current.status).toBe("idle");
    expect(noSelection.result.current.meta).toBeNull();

    const noGroup = renderHook(() => useLandContextQuery(POINT, ALL_OFF));
    expect(noGroup.result.current.status).toBe("idle");
    expect(queries.pointUseQuery).toHaveBeenLastCalledWith(
      { lon: -116.2, lat: 43.6 },
      { enabled: false }
    );
  });

  it("is loading until a response arrives and error when the transport fails", () => {
    queries.point.isLoading = true;
    const loading = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(loading.result.current.status).toBe("loading");
    expect(queries.pointUseQuery).toHaveBeenLastCalledWith(
      { lon: -116.2, lat: 43.6 },
      { enabled: true }
    );

    queries.point = { data: undefined, isLoading: false, isError: true, error: new Error("offline") };
    const failed = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(failed.result.current.status).toBe("error");
    expect(failed.result.current.isError).toBe(true);
  });

  it("reports a budget refusal as settled meta with no features", () => {
    queries.point.data = {
      status: "budget_exceeded",
      reason: "outside_pilot_states",
      limit: 3,
      requested: null,
    };
    const { result } = renderHook(() => useLandContextQuery(POINT, BLM_ON));
    expect(result.current.status).toBe("settled");
    expect(result.current.data).toEqual([]);
    expect(result.current.meta?.budgetExceeded).toEqual({
      reason: "outside_pilot_states",
      limit: 3,
      requested: null,
    });
  });
});
