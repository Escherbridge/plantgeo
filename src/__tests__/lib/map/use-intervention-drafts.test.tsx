import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";

const mocks = vi.hoisted(() => ({
  sessionStatus: "unauthenticated" as "authenticated" | "unauthenticated" | "loading",
  listMySubmissions: vi.fn(),
  listProposed: vi.fn(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => ({ status: mocks.sessionStatus }),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      listMySubmissions: { useQuery: mocks.listMySubmissions },
      listProposed: { useQuery: mocks.listProposed },
    },
  },
}));

import {
  invalidateInterventionDraftsOverlay,
  useInterventionDraftsOverlay,
} from "@/lib/map/use-intervention-drafts";

function queryResult(overrides: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, ...overrides };
}

beforeEach(() => {
  mocks.sessionStatus = "unauthenticated";
  mocks.listMySubmissions.mockReturnValue(queryResult());
  mocks.listProposed.mockReturnValue(queryResult());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useInterventionDraftsOverlay auth gating", () => {
  it("does not enable either fetch while signed out", () => {
    renderHook(() => useInterventionDraftsOverlay());

    expect(mocks.listMySubmissions.mock.calls.at(-1)?.[1]).toMatchObject({ enabled: false });
    expect(mocks.listProposed.mock.calls.at(-1)?.[1]).toMatchObject({ enabled: false });
  });

  it("returns an empty, disabled result while signed out even if data is somehow cached", () => {
    mocks.listMySubmissions.mockReturnValue(
      queryResult({ data: [{ id: "a", status: "pending_review", properties: { geometry: { type: "Point", coordinates: [1, 2] } } }] })
    );
    const { result } = renderHook(() => useInterventionDraftsOverlay());

    expect(result.current.isEnabled).toBe(false);
    expect(result.current.geojson.features).toEqual([]);
  });

  it("enables both fetches once signed in", () => {
    mocks.sessionStatus = "authenticated";
    renderHook(() => useInterventionDraftsOverlay());

    expect(mocks.listMySubmissions.mock.calls.at(-1)?.[1]).toMatchObject({ enabled: true });
    expect(mocks.listProposed.mock.calls.at(-1)?.[1]).toMatchObject({ enabled: true });
  });
});

describe("useInterventionDraftsOverlay merge and dedupe", () => {
  beforeEach(() => {
    mocks.sessionStatus = "authenticated";
  });

  it("carries the caller's own drawn geometry, tagged isOwn and with its real status", () => {
    mocks.listMySubmissions.mockReturnValue(
      queryResult({
        data: [
          {
            id: "own-1",
            status: "pending_review",
            properties: {
              name: "Ridge plot",
              type: "reforestation",
              category: "land",
              geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
            },
          },
        ],
      })
    );
    mocks.listProposed.mockReturnValue(queryResult({ data: [] }));

    const { result } = renderHook(() => useInterventionDraftsOverlay());

    expect(result.current.geojson.features).toHaveLength(1);
    const [feature] = result.current.geojson.features;
    expect(feature.geometry.type).toBe("Polygon");
    expect(feature.properties).toMatchObject({
      id: "own-1",
      isOwn: true,
      status: "pending_review",
      category: "land",
    });
  });

  it("carries another contributor's proposal as a centroid point, tagged isOwn: false", () => {
    mocks.listMySubmissions.mockReturnValue(queryResult({ data: [] }));
    mocks.listProposed.mockReturnValue(
      queryResult({
        data: [
          {
            id: "other-1",
            name: "Cloud seeding trial",
            type: "cloud_seeding",
            category: "air",
            longitude: -116.2,
            latitude: 43.6,
          },
        ],
      })
    );

    const { result } = renderHook(() => useInterventionDraftsOverlay());

    expect(result.current.geojson.features).toHaveLength(1);
    const [feature] = result.current.geojson.features;
    expect(feature.geometry).toEqual({ type: "Point", coordinates: [-116.2, 43.6] });
    expect(feature.properties).toMatchObject({ id: "other-1", isOwn: false, category: "air" });
  });

  it("keeps the caller's own drawn-geometry copy over listProposed's centroid for the same row", () => {
    mocks.listMySubmissions.mockReturnValue(
      queryResult({
        data: [
          {
            id: "dup-1",
            status: "pending_review",
            properties: {
              name: "Dup",
              type: "reforestation",
              category: "land",
              geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
            },
          },
        ],
      })
    );
    mocks.listProposed.mockReturnValue(
      queryResult({
        data: [
          {
            id: "dup-1",
            name: "Dup",
            type: "reforestation",
            category: "land",
            longitude: 0.5,
            latitude: 0.5,
          },
        ],
      })
    );

    const { result } = renderHook(() => useInterventionDraftsOverlay());

    expect(result.current.geojson.features).toHaveLength(1);
    expect(result.current.geojson.features[0].geometry.type).toBe("Polygon");
  });

  it("skips a proposed row with no centroid instead of drawing a null-island point", () => {
    mocks.listMySubmissions.mockReturnValue(queryResult({ data: [] }));
    mocks.listProposed.mockReturnValue(
      queryResult({
        data: [{ id: "no-loc", name: "n", type: "biochar", category: "land", longitude: null, latitude: null }],
      })
    );

    const { result } = renderHook(() => useInterventionDraftsOverlay());
    expect(result.current.geojson.features).toEqual([]);
  });
});

describe("invalidateInterventionDraftsOverlay", () => {
  it("invalidates both underlying queries", async () => {
    const listMySubmissionsInvalidate = vi.fn().mockResolvedValue(undefined);
    const listProposedInvalidate = vi.fn().mockResolvedValue(undefined);

    await invalidateInterventionDraftsOverlay({
      interventions: {
        listMySubmissions: { invalidate: listMySubmissionsInvalidate },
        listProposed: { invalidate: listProposedInvalidate },
      },
    });

    expect(listMySubmissionsInvalidate).toHaveBeenCalledTimes(1);
    expect(listProposedInvalidate).toHaveBeenCalledTimes(1);
  });
});
