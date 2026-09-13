import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";

/**
 * What is under test is the BAND DECISION and the query wiring around it -- that a zoom selects
 * detail or aggregate against this plane's own floor, that the viewport reaches the query key,
 * and that the plane's four states are read honestly. The tRPC client is stubbed rather than
 * driven, for the same reason `useParquetFireDetections.test.ts` stubs it: how a bbox is
 * computed is `viewport-bbox.ts`'s subject, not this hook's.
 */
const inputs = vi.hoisted(() => ({
  useQuery: vi.fn(),
  /** Which toggles the registry withholds. Empty in every case but one. */
  withheld: new Set<string>(),
}));

vi.mock("@/lib/map/layer-registry", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/map/layer-registry")>();
  return {
    ...actual,
    isLayerPermanentlyWithheld: (toggleId: string) => inputs.withheld.has(toggleId),
  };
});

vi.mock("@/lib/trpc/client", () => ({
  trpc: { environmental: { getBotanicalOccurrences: { useQuery: inputs.useQuery } } },
}));

import {
  botanicalBandForZoom,
  useBotanicalOccurrencesQuery,
} from "@/hooks/useViewportProxiedLayers";
import { BOTANICAL_DETAIL_MIN_ZOOM } from "@/lib/botanical-occurrences";

/** The inputs the query was actually called with, and the options beside them. */
function lastCall() {
  const call = inputs.useQuery.mock.calls.at(-1);
  if (call === undefined) throw new Error("the query was never called");
  return { input: call[0] as Record<string, unknown>, options: call[1] as Record<string, unknown> };
}

const VIEWPORT_BBOX = "-123.5,45.2,-122.1,46.4";

beforeEach(() => {
  inputs.withheld = new Set();
  inputs.useQuery.mockReset();
  inputs.useQuery.mockReturnValue({ data: undefined, isFetching: false, isPlaceholderData: false });
});

afterEach(() => {
  cleanup();
});

describe("botanicalBandForZoom", () => {
  /**
   * The floor is the PLANE's (`DETAIL_ZOOM_FLOOR = 11` server-side), not a `ZOOM_TIERS` rung.
   * These two cases are the whole reason the hook does not use `resolveZoomTier`: 11 and 12 both
   * resolve to the ladder's z9 rung, so a tier-based band would send a detail request to the
   * aggregate reader and draw grid cells where a reader asked for specimens.
   */
  it("answers detail at and above the plane's own floor, including the zooms the tier ladder would round down", () => {
    expect(botanicalBandForZoom(BOTANICAL_DETAIL_MIN_ZOOM)).toBe("detail");
    expect(botanicalBandForZoom(11)).toBe("detail");
    expect(botanicalBandForZoom(12)).toBe("detail");
    expect(botanicalBandForZoom(13)).toBe("detail");
    expect(botanicalBandForZoom(18)).toBe("detail");
  });

  it("answers aggregate below the floor", () => {
    expect(botanicalBandForZoom(BOTANICAL_DETAIL_MIN_ZOOM - 0.01)).toBe("aggregate");
    expect(botanicalBandForZoom(10)).toBe("aggregate");
    expect(botanicalBandForZoom(9)).toBe("aggregate");
    expect(botanicalBandForZoom(0)).toBe("aggregate");
  });

  /** The service does its own `int()` coercion, so a fractional map zoom must not be rounded UP. */
  it("does not round a fractional zoom up across the floor", () => {
    expect(botanicalBandForZoom(10.9)).toBe("aggregate");
  });
});

describe("useBotanicalOccurrencesQuery", () => {
  it("sends the viewport bbox and the raw zoom, and enables the query", () => {
    renderHook(() => useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom: 12 }));

    const { input, options } = lastCall();
    expect(input.bbox).toBe(VIEWPORT_BBOX);
    // The RAW zoom, not a tier: 12 must arrive as 12, not as the ladder's 9.
    expect(input.zoom).toBe(12);
    expect(options.enabled).toBe(true);
  });

  /**
   * A pan is a new bbox in the same key, which is the whole mechanism by which a viewport change
   * triggers a fetch -- there is no imperative refetch anywhere in this lane.
   */
  it("re-keys the query when the viewport moves", () => {
    const { rerender } = renderHook(
      ({ bbox }: { bbox: string }) =>
        useBotanicalOccurrencesQuery(bbox, { enabled: true, zoom: 12 }),
      { initialProps: { bbox: VIEWPORT_BBOX } }
    );
    expect(lastCall().input.bbox).toBe(VIEWPORT_BBOX);

    rerender({ bbox: "-120,44,-119,45" });
    expect(lastCall().input.bbox).toBe("-120,44,-119,45");
  });

  /** A zoom across the floor is a different ANSWER, not a different aggregation, so it re-keys. */
  it("re-keys the query when the zoom crosses the detail floor", () => {
    const { rerender } = renderHook(
      ({ zoom }: { zoom: number }) =>
        useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom }),
      { initialProps: { zoom: 8 } }
    );
    expect(lastCall().input.zoom).toBe(8);

    rerender({ zoom: 12 });
    expect(lastCall().input.zoom).toBe(12);
  });

  it("disables the query when no viewport bbox is expressible", () => {
    renderHook(() => useBotanicalOccurrencesQuery(null, { enabled: true, zoom: 12 }));
    expect(lastCall().options.enabled).toBe(false);
  });

  it("disables the query when the caller's gate is closed", () => {
    renderHook(() => useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: false, zoom: 12 }));
    expect(lastCall().options.enabled).toBe(false);
  });

  /**
   * All three toggles share ONE read, so withholding one of them must not take the other two
   * off the map -- only withholding every one of them means nothing is drawable.
   */
  it("stays enabled while any botanical toggle is still permitted", () => {
    inputs.withheld = new Set(["botanical-occurrences"]);
    renderHook(() => useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom: 4 }));
    expect(lastCall().options.enabled).toBe(true);
  });

  it("disables the query only when every botanical toggle is withheld", () => {
    inputs.withheld = new Set([
      "botanical-occurrences",
      "botanical-richness",
      "botanical-collection-effort",
    ]);
    renderHook(() => useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom: 4 }));
    expect(lastCall().options.enabled).toBe(false);
  });

  /** Empty filter strings are the store's "unset"; sending `family=` would filter to nothing. */
  it("passes the caller's filters through without inventing values", () => {
    renderHook(() =>
      useBotanicalOccurrencesQuery(VIEWPORT_BBOX, {
        enabled: true,
        zoom: 12,
        family: "Rosaceae",
        spatialQuality: "all",
      })
    );

    const { input } = lastCall();
    expect(input.family).toBe("Rosaceae");
    expect(input.spatialQuality).toBe("all");
    expect(input.taxonConceptId).toBeUndefined();
    expect(input.collectionKey).toBeUndefined();
  });

  /** A retained frame is what keeps the map from blanking for a round trip on every pan. */
  it("retains the previous answer while the next one loads", () => {
    renderHook(() => useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom: 12 }));
    expect(lastCall().options.placeholderData).toBeDefined();
  });

  /**
   * The plane's four states are RETURNED, never thrown, so each reaches the caller as data on a
   * SUCCESSFUL query. A refusal surfacing as a query error would be indistinguishable from an
   * outage, which is the one distinction this plane's own union exists to preserve.
   */
  it.each([
    ["refused", { state: "refused", reason: "bbox_too_wide", detail: "...", note: "Zoom in." }],
    ["unavailable", { state: "unavailable", reason: "no_generation", note: "Nothing published." }],
  ])("surfaces a %s answer as data rather than as a failed query", (_label, answer) => {
    inputs.useQuery.mockReturnValue({
      data: answer,
      isError: false,
      isFetching: false,
      isPlaceholderData: false,
    });

    const { result } = renderHook(() =>
      useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom: 12 })
    );

    expect(result.current.isError).toBe(false);
    expect(result.current.data).toEqual(answer);
  });

  it("passes a detail answer through with its features intact", () => {
    const answer = {
      state: "detail",
      releaseSetId: "ubc-v16.43",
      publishedAt: "2026-09-01T00:00:00Z",
      features: [{ occurrenceId: "a" }],
      truncated: false,
      counts: { returned: 1, matched: 1, withheld: 0, nonspatial: 0, excludedByQc: 0 },
    };
    inputs.useQuery.mockReturnValue({ data: answer, isError: false, isFetching: false });

    const { result } = renderHook(() =>
      useBotanicalOccurrencesQuery(VIEWPORT_BBOX, { enabled: true, zoom: 12 })
    );

    expect(result.current.data).toEqual(answer);
  });
});
