/**
 * The automatic land-context viewport lane, end to end through its consumer.
 *
 * Two obligations from style review W3's blocker B1, both proved here: a region that binds no
 * source for the plane issues NO request at all (`enabled: false` reaches react-query), and every
 * state the lane can be in produces a distinct caption -- the defect was that four of five
 * rendered nothing, which is indistinguishable from a read that failed.
 *
 * The response decode is the click lane's own `toResults`, so a matched result drawn here is
 * proof the two lanes share one decoder.
 */
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LandContextResult } from "@/lib/environmental/land-context-contract";
import type { SliderCapabilities } from "@/types/time-slider";

interface QueryState {
  data: unknown;
  isError: boolean;
  isPlaceholderData: boolean;
}

const lane = vi.hoisted(() => ({
  /** What the mocked `resolveBoundaryInArea` answers with. */
  query: { data: undefined, isError: false, isPlaceholderData: false } as QueryState,
  /** The options each render handed react-query, newest last -- where `enabled` is read. */
  queryOptions: [] as Record<string, unknown>[],
  /** The viewport the hook measures; `bbox: null` is the unmeasurable camera. */
  viewport: { zoom: 13, bbox: "-116.3,43.5,-116.1,43.7" } as { zoom: number; bbox: string | null },
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    landContext: {
      resolveBoundaryInArea: {
        useQuery: (_input: unknown, options: Record<string, unknown>) => {
          lane.queryOptions.push(options);
          return lane.query;
        },
      },
    },
  },
}));

vi.mock("@/hooks/useViewportProxiedLayers", () => ({
  useViewportBounds: () => lane.viewport,
  PROXIED_RETRY_COUNT: 1,
}));

import { useLandContextViewportBoundaries } from "@/components/map/layer-manager/useLandContextViewportBoundaries";
import { useLandContextStore } from "@/stores/land-context-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";

type BoundaryResult = LandContextResult & { geometry: GeoJSON.Geometry | null };

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

function matchedBoundary(): BoundaryResult {
  return {
    coverageState: "matched",
    sourceFeature: {
      sourceNamespace: "blm-national-sma",
      nativeFeatureKey: "SMA-000123",
      nativeFeatureVersion: null,
      familyType: "blm_surface_management",
      interestType: "surface_management",
      state: "ID",
      county: "Ada",
      geometryWkb: "0103...",
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
    matchedRegionOrOverlap: { kind: "bbox_intersection", description: "polygon intersects bbox" },
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: [],
    isCurrentReferenceOnly: true,
    geometry: POLYGON,
  };
}

function coverageOnly(
  coverageState: LandContextResult["coverageState"],
  gaps: string[] = []
): BoundaryResult {
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

/**
 * Capabilities stating one binding, which is the ONLY evidence strong enough to turn the lane on:
 * the PNW manifest binds no `land-context` layer, so silence leaves it unbound.
 */
function capabilitiesBinding(binding: "bound_regional" | "unbound"): SliderCapabilities {
  return {
    layerBindings: [
      {
        layerSlug: "land-context",
        binding,
        sourceSlug: binding === "unbound" ? null : "pnw-reference-plane",
        reason: binding === "unbound" ? "no_source_bound_in_region" : null,
      },
    ],
  } as unknown as SliderCapabilities;
}

/** The newest `enabled` the hook handed react-query. */
function latestEnabled(): unknown {
  return lane.queryOptions.at(-1)?.enabled;
}

beforeEach(() => {
  lane.query = { data: undefined, isError: false, isPlaceholderData: false };
  lane.queryOptions.length = 0;
  lane.viewport = { zoom: 13, bbox: "-116.3,43.5,-116.1,43.7" };
  useLandContextStore.setState({
    enabledGroups: {
      "parcels-land-use": false,
      "electric-utility-territories": false,
      "blm-lands": true,
      "state-managed-lands": false,
    },
  });
  useTimeSliderStore.setState({ capabilities: capabilitiesBinding("bound_regional") });
});

describe("the region-binding gate", () => {
  it("issues no request at all when this region binds no source for the plane", () => {
    useTimeSliderStore.setState({ capabilities: capabilitiesBinding("unbound") });

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(latestEnabled()).toBe(false);
    expect(result.current.state).toBe("layer_unbound_in_region");
    expect(result.current.fault?.layerId).toBe("land-context-unbound-in-region");
  });

  it("treats manifest silence as unbound, because the manifest states the complete set", () => {
    // No capabilities at all: the PNW manifest's `enabledLayers` has no `land-context` entry, and
    // absence from a bundled manifest is a claim rather than a deploy-window gap.
    useTimeSliderStore.setState({ capabilities: null });

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(latestEnabled()).toBe(false);
    expect(result.current.state).toBe("layer_unbound_in_region");
  });

  it("issues no request when no group is switched on, and says nothing about it", () => {
    useLandContextStore.setState({
      enabledGroups: {
        "parcels-land-use": false,
        "electric-utility-territories": false,
        "blm-lands": false,
        "state-managed-lands": false,
      },
    });

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(latestEnabled()).toBe(false);
    expect(result.current.state).toBe("no_group_enabled");
    // The single silence in the whole table: the reader asked for nothing.
    expect(result.current.fault).toBeNull();
  });

  it("asks once the plane is bound and a group is on", () => {
    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(latestEnabled()).toBe(true);
    expect(result.current.state).toBe("reading");
  });
});

describe("every state reaches a reader as its own caption", () => {
  it("names an unmeasurable viewport rather than rendering nothing", () => {
    lane.viewport = { zoom: 13, bbox: null };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.state).toBe("viewport_unavailable");
    expect(result.current.fault?.layerId).toBe("land-context-viewport-unavailable");
    expect(latestEnabled()).toBe(false);
  });

  it("names a viewport no published rung serves", () => {
    // A zoom below the ladder's floor tier: `resolveZoomTier` refuses it rather than guessing z0,
    // so no rung is selectable and the lane must SAY so instead of coarsening silently.
    lane.viewport = { zoom: -1, bbox: "-116.3,43.5,-116.1,43.7" };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.state).toBe("no_rung_serves_this_viewport");
    expect(result.current.fault?.layerId).toBe("land-context-no-rung-serves-viewport");
    expect(latestEnabled()).toBe(false);
  });

  it("names an over-budget AOI a rung would otherwise admit", () => {
    // 10 x 10 square degrees: the z9 rung admits 100, the AOI budget caps at 1.
    lane.viewport = { zoom: 9, bbox: "-120,40,-110,50" };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.state).toBe("area_over_budget");
    expect(result.current.fault?.layerId).toBe("land-context-area-over-budget");
    expect(result.current.fault?.message).toContain("Zoom in");
    expect(latestEnabled()).toBe(false);
  });

  it("reports a failed read as a FAULT, never as an empty view", () => {
    lane.query = { data: undefined, isError: true, isPlaceholderData: false };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.readPhase).toBe("error");
    expect(result.current.fault?.tone).toBe("fault");
    expect(result.current.fault?.layerId).toBe("land-context-request-failed");
    expect(result.current.geoJSON).toBeNull();
  });

  it("says the read is in flight rather than staying silent", () => {
    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.readPhase).toBe("loading");
    expect(result.current.fault?.layerId).toBe("land-context-reading");
  });

  it("labels a retained frame as the PREVIOUS view's answer", () => {
    lane.query = {
      data: { status: "ok", data: [matchedBoundary()] },
      isError: false,
      isPlaceholderData: true,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.readPhase).toBe("stale");
    expect(result.current.fault?.layerId).toBe("land-context-retained");
  });

  it("states a governed absence in the plane's own words, never an empty canvas", () => {
    lane.query = {
      data: {
        status: "ok",
        data: [coverageOnly("source_unbound_for_region", ["no Parquet lane wired in yet"])],
      },
      isError: false,
      isPlaceholderData: false,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.readPhase).toBe("empty");
    expect(result.current.fault?.layerId).toBe("land-context-source-unbound");
    expect(result.current.fault?.tone).toBe("notice");
    expect(result.current.fault?.message).toContain("no admitted source is bound");
    expect(result.current.geoJSON).toBeNull();
  });

  it("distinguishes a different empty coverage state from an unbound source", () => {
    lane.query = {
      data: { status: "ok", data: [coverageOnly("no_match_in_proven_coverage")] },
      isError: false,
      isPlaceholderData: false,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.fault?.layerId).toBe("land-context-empty");
    expect(result.current.fault?.message).toContain("coverage here is proven");
  });

  it("quotes the plane's own refusal when it declines to read the view", () => {
    lane.query = {
      data: { status: "budget_exceeded", reason: "aoi_area_exceeds_limit" },
      isError: false,
      isPlaceholderData: false,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.fault?.layerId).toBe("land-context-read-refused");
    expect(result.current.fault?.message).toContain("aoi_area_exceeds_limit");
  });
});

describe("the decode is the click lane's", () => {
  it("draws a matched boundary and counts it in the caption", () => {
    lane.query = {
      data: { status: "ok", data: [matchedBoundary()] },
      isError: false,
      isPlaceholderData: false,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.readPhase).toBe("success");
    expect(result.current.features).toHaveLength(1);
    expect(result.current.geoJSON?.features[0]).toMatchObject({
      geometry: POLYGON,
      properties: { group: "blm-lands" },
    });
    expect(result.current.fault?.layerId).toBe("land-context-drawn");
    expect(result.current.fault?.message).toContain("1 land-context boundaries");
  });

  it("drops a matched boundary whose group the reader switched off", () => {
    useLandContextStore.setState({
      enabledGroups: {
        "parcels-land-use": true,
        "electric-utility-territories": false,
        "blm-lands": false,
        "state-managed-lands": false,
      },
    });
    lane.query = {
      data: { status: "ok", data: [matchedBoundary()] },
      isError: false,
      isPlaceholderData: false,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.features).toHaveLength(0);
    expect(result.current.geoJSON).toBeNull();
  });

  it("keeps features and a coverage statement apart as a PARTIAL answer", () => {
    lane.query = {
      data: {
        status: "ok",
        data: [matchedBoundary(), coverageOnly("partial_area_coverage")],
      },
      isError: false,
      isPlaceholderData: false,
    };

    const { result } = renderHook(() => useLandContextViewportBoundaries());

    expect(result.current.readPhase).toBe("partial");
    expect(result.current.features).toHaveLength(1);
    expect(result.current.fault?.layerId).toBe("land-context-partial");
    expect(result.current.fault?.message).toContain("not the whole answer");
  });
});
