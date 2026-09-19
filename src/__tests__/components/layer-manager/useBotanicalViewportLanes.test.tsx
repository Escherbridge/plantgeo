/**
 * The single-upstream-read guarantee at the detail band (W8-D, 2026-09-18).
 *
 * Through W3-A/W5-D both occurrence lanes ran together whenever the UBC toggle was on: the tRPC
 * lane for the release-set pin and the filters-panel state, the proxy lane for the checksum-bound
 * pointer and `servingRung`. That was two upstream round trips for one screen. This file pins the
 * fix: at the detail band exactly ONE of the two hooks is ever `enabled`, regardless of which of
 * `occurrencesVisible`/`gbifVisible` is on, and at the aggregate band it is the other one.
 *
 * Both hooks are mocked WHOLE, mirroring `useLandContextViewportBoundaries.test.tsx` -- this file
 * asserts the GATE each lane is called with, not the fetch mechanics either hook already owns its
 * own tests for.
 */
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BotanicalOccurrencesSnapshot } from "@/hooks/useBotanicalOccurrences";

interface TrpcQueryState {
  data: unknown;
  isError: boolean;
  isFetching: boolean;
  isPlaceholderData: boolean;
  isSuccess: boolean;
}

const lane = vi.hoisted(() => ({
  /** Which band `botanicalBandForZoom` answers with for the zoom under test. */
  band: "detail" as "detail" | "aggregate",
  /** The tRPC lane's canned react-query result. */
  trpcQuery: {
    data: undefined,
    isError: false,
    isFetching: false,
    isPlaceholderData: false,
    isSuccess: false,
  } as TrpcQueryState,
  /** The proxy lane's canned snapshot. */
  proxySnapshot: {
    phase: "idle",
    answer: null,
    error: null,
    isStale: false,
    isPartial: false,
    band: "detail",
    servingBand: "detail",
  } as BotanicalOccurrencesSnapshot,
  /** Every options object each lane was called with, newest last -- where `enabled` is read. */
  trpcCalls: [] as Record<string, unknown>[],
  proxyCalls: [] as Record<string, unknown>[],
}));

/**
 * The tRPC lane, modelled as react-query leaves it AND as the hook now publishes it.
 *
 * `data`/`isError` are the raw observer fields a `keepPreviousData` observer keeps serving after
 * it is disabled; `answer`/`isAnswerLive`/`isError` are the `LiveViewportRead` the real hook
 * returns (`src/hooks/useViewportProxiedLayers.ts:158-216`). Both are handed back deliberately:
 * the retained frame stays reachable here, so a consumer that reads it instead of the published
 * liveness fails these cases rather than passing on a mock that cannot express the defect.
 *
 * Liveness mirrors the real composition's two live conjuncts -- the caller's gate and a
 * measurable viewport. The third (the governance conjunction) is static per build and is pinned
 * against the real hook in `src/__tests__/hooks/useBotanicalOccurrences.test.ts`, which asserts
 * the published liveness IS the observer's `enabled` for every combination.
 */
vi.mock("@/hooks/useViewportProxiedLayers", () => ({
  botanicalBandForZoom: () => lane.band,
  useBotanicalOccurrencesQuery: (bbox: unknown, options: Record<string, unknown>) => {
    lane.trpcCalls.push({ ...options, bbox });
    const isAnswerLive = options.enabled === true && bbox !== null && bbox !== undefined;
    return {
      ...lane.trpcQuery,
      answer: isAnswerLive ? lane.trpcQuery.data : undefined,
      isAnswerLive,
      isError: isAnswerLive && lane.trpcQuery.isError,
    };
  },
}));

vi.mock("@/hooks/useBotanicalOccurrences", () => ({
  useBotanicalOccurrences: (options: Record<string, unknown>) => {
    lane.proxyCalls.push(options);
    return lane.proxySnapshot;
  },
}));

import { useBotanicalViewportLanes } from "@/components/map/layer-manager/useBotanicalViewportLanes";
import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";

const BASE_OPTIONS = {
  bbox: "-123.2,49.1,-123.0,49.3",
  zoom: 13,
  occurrencesVisible: false,
  richnessVisible: false,
  effortVisible: false,
  gbifVisible: false,
};

function lastEnabled(calls: Record<string, unknown>[]): boolean {
  return calls.length > 0 && calls[calls.length - 1].enabled === true;
}

describe("useBotanicalViewportLanes: single upstream read", () => {
  beforeEach(() => {
    lane.trpcCalls = [];
    lane.proxyCalls = [];
    lane.band = "detail";
    // Both canned lanes reset too: the release-set-pin cases below drive them across renders, and
    // a leaked answer would let a later case pass on the previous one's data.
    lane.trpcQuery = {
      data: undefined,
      isError: false,
      isFetching: false,
      isPlaceholderData: false,
      isSuccess: false,
    };
    lane.proxySnapshot = {
      phase: "idle",
      answer: null,
      error: null,
      isStale: false,
      isPartial: false,
      band: "detail",
      servingBand: "detail",
    };
    useBotanicalOccurrenceStore.getState().resetFilters();
    useBotanicalOccurrenceStore.getState().setLastResponse(null);
  });

  it("reads only the proxy lane at the detail band with the UBC toggle on", () => {
    lane.band = "detail";
    renderHook(() => useBotanicalViewportLanes({ ...BASE_OPTIONS, occurrencesVisible: true }));
    expect(lastEnabled(lane.proxyCalls)).toBe(true);
    expect(lastEnabled(lane.trpcCalls)).toBe(false);
  });

  it("reads only the proxy lane at the detail band with only the GBIF toggle on", () => {
    lane.band = "detail";
    renderHook(() => useBotanicalViewportLanes({ ...BASE_OPTIONS, gbifVisible: true }));
    expect(lastEnabled(lane.proxyCalls)).toBe(true);
    expect(lastEnabled(lane.trpcCalls)).toBe(false);
  });

  it("reads only the proxy lane at the detail band with both toggles on", () => {
    lane.band = "detail";
    renderHook(() =>
      useBotanicalViewportLanes({ ...BASE_OPTIONS, occurrencesVisible: true, gbifVisible: true })
    );
    expect(lastEnabled(lane.proxyCalls)).toBe(true);
    expect(lastEnabled(lane.trpcCalls)).toBe(false);
  });

  it("issues no upstream read at the detail band when neither toggle is on", () => {
    lane.band = "detail";
    renderHook(() => useBotanicalViewportLanes({ ...BASE_OPTIONS }));
    expect(lastEnabled(lane.proxyCalls)).toBe(false);
    expect(lastEnabled(lane.trpcCalls)).toBe(false);
  });

  it("reads only the tRPC lane at a coarse band with an aggregate toggle on", () => {
    lane.band = "aggregate";
    renderHook(() =>
      useBotanicalViewportLanes({ ...BASE_OPTIONS, zoom: 6, richnessVisible: true })
    );
    expect(lastEnabled(lane.trpcCalls)).toBe(true);
    expect(lastEnabled(lane.proxyCalls)).toBe(false);
  });

  it("re-reads exactly the one active lane on a viewport change, never the other", () => {
    lane.band = "detail";
    const { rerender } = renderHook(
      (props: { bbox: string }) =>
        useBotanicalViewportLanes({ ...BASE_OPTIONS, occurrencesVisible: true, bbox: props.bbox }),
      { initialProps: { bbox: BASE_OPTIONS.bbox } }
    );
    const proxyCallsBeforePan = lane.proxyCalls.length;
    rerender({ bbox: "-123.4,49.0,-123.1,49.4" });
    expect(lane.proxyCalls.length).toBeGreaterThan(proxyCallsBeforePan);
    expect(lane.trpcCalls.every((options) => options.enabled !== true)).toBe(true);
  });
});

/** An aggregate-band tRPC answer, trimmed to the fields this hook reads off one. */
function aggregateAnswer(releaseSetId: string) {
  return {
    state: "aggregate",
    releaseSetId,
    publishedAt: "2026-09-01T00:00:00Z",
    supportId: "grid-0.25",
    cells: [],
    counts: { returned: 0, matched: 0 },
    truncated: false,
  };
}

/** A detail-band PROXY answer, likewise trimmed to what the hook reads. */
function proxyDetailSnapshot(releaseSetId: string): BotanicalOccurrencesSnapshot {
  return {
    phase: "success",
    answer: {
      state: "detail",
      releaseSetId,
      publishedAt: "2026-09-17T00:00:00Z",
      servingRung: "detail",
      truncated: false,
      features: [],
      counts: { returned: 0, matched: 0, withheld: 0 },
    },
    error: null,
    isStale: false,
    isPartial: false,
    band: "detail",
    servingBand: "detail",
  } as unknown as BotanicalOccurrencesSnapshot;
}

function pinnedReleaseSetId(): string | null {
  return useBotanicalOccurrenceStore.getState().filters.release_set_id;
}

/**
 * The release-set pin names the generation the DRAWN cells came from (style review W8, B3).
 *
 * The tRPC lane is configured `placeholderData: keepPreviousData`, and a disabled react-query
 * observer keeps serving the previous key's answer -- so at the detail band the raw query `data`
 * still holds the aggregate answer a coarse viewport landed. The pre-fix code chose the lane by
 * `botanicalResult !== undefined`, which that retained answer satisfies, so a zoom from 6 to 11
 * pinned and displayed a generation the points on screen were never read from.
 *
 * The band alone was not enough (style review W9, S1): the caller's toggle gate is false at the
 * detail band AND at the aggregate band whenever both aggregate toggles are off, so scoping by
 * band closed one case of a two-case defect.
 *
 * The caller's toggle gate was not enough either (style review W10, B1): it is one conjunct of an
 * enablement composed inside the read, and the missing one -- a measurable viewport -- is DYNAMIC.
 * The discriminator is now the read's own published liveness
 * (`src/hooks/useViewportProxiedLayers.ts:158-183`), which no consumer re-derives; the
 * collapsed-container case below is the one the toggle-gate guard still got wrong.
 */
describe("useBotanicalViewportLanes: the pin follows the lane that drew the cells", () => {
  beforeEach(() => {
    lane.trpcCalls = [];
    lane.proxyCalls = [];
    useBotanicalOccurrenceStore.getState().resetFilters();
    useBotanicalOccurrenceStore.getState().setLastResponse(null);
  });

  it("pins the aggregate answer's generation at the aggregate band", () => {
    lane.band = "aggregate";
    lane.trpcQuery = {
      data: aggregateAnswer("release-aggregate"),
      isError: false,
      isFetching: false,
      isPlaceholderData: false,
      isSuccess: true,
    };
    renderHook(() => useBotanicalViewportLanes({ ...BASE_OPTIONS, zoom: 6, richnessVisible: true }));

    expect(pinnedReleaseSetId()).toBe("release-aggregate");
    expect(useBotanicalOccurrenceStore.getState().lastResponse?.state).toBe("aggregate");
  });

  it("withholds the pin across a zoom to the detail band while the proxy read is in flight", () => {
    lane.band = "aggregate";
    lane.trpcQuery = {
      data: aggregateAnswer("release-aggregate"),
      isError: false,
      isFetching: false,
      isPlaceholderData: false,
      isSuccess: true,
    };
    const { rerender } = renderHook(() =>
      useBotanicalViewportLanes({ ...BASE_OPTIONS, occurrencesVisible: true, richnessVisible: true })
    );
    expect(pinnedReleaseSetId()).toBe("release-aggregate");

    // The zoom crosses the detail floor. The tRPC observer is now disabled but STILL holds its
    // retained answer, exactly as `keepPreviousData` leaves it; the proxy lane has not answered yet.
    lane.band = "detail";
    lane.proxySnapshot = { ...lane.proxySnapshot, phase: "loading", answer: null, isStale: false };
    rerender();

    expect(pinnedReleaseSetId()).toBeNull();
    expect(useBotanicalOccurrenceStore.getState().lastResponse).toBeNull();
  });

  it("pins the proxy answer's generation once the detail read lands", () => {
    lane.band = "aggregate";
    lane.trpcQuery = {
      data: aggregateAnswer("release-aggregate"),
      isError: false,
      isFetching: false,
      isPlaceholderData: false,
      isSuccess: true,
    };
    const { rerender } = renderHook(() =>
      useBotanicalViewportLanes({ ...BASE_OPTIONS, occurrencesVisible: true, richnessVisible: true })
    );

    lane.band = "detail";
    lane.proxySnapshot = proxyDetailSnapshot("release-detail");
    rerender();

    expect(pinnedReleaseSetId()).toBe("release-detail");
    expect(useBotanicalOccurrenceStore.getState().lastResponse).toMatchObject({
      state: "detail",
      releaseSetId: "release-detail",
    });
  });

  it("withholds the pin at the AGGREGATE band while both aggregate toggles are off", () => {
    // W8's B3 was fixed by scoping the retained answer to the band, which closed the detail case
    // and left this one (style review W9, S1). At zoom 6 with only the UBC occurrences toggle on,
    // the caller's toggle gate is false -- the tRPC observer is DISABLED -- yet
    // `keepPreviousData` still
    // hands back the aggregate answer an earlier viewport landed. Nothing botanical is drawn at
    // all here: the occurrences layer is band-gated off below the detail floor, and the two
    // aggregate layers are switched off. A pin under those conditions names a generation for a map
    // showing no botanical cells. Under the band-only guard every assertion below reads
    // "release-aggregate"/"aggregate" instead.
    lane.band = "aggregate";
    lane.trpcQuery = {
      // `truncated` and `note` carry values the fix must drop too, so every assertion below is one
      // the band-only guard fails rather than one that reads false for a second reason.
      data: { ...aggregateAnswer("release-aggregate"), truncated: true, note: "capped at 5000 cells" },
      isError: false,
      isFetching: false,
      isPlaceholderData: true,
      isSuccess: true,
    };
    const { result } = renderHook(() =>
      useBotanicalViewportLanes({ ...BASE_OPTIONS, zoom: 6, occurrencesVisible: true })
    );

    expect(lastEnabled(lane.trpcCalls)).toBe(false);
    expect(pinnedReleaseSetId()).toBeNull();
    expect(useBotanicalOccurrenceStore.getState().lastResponse).toBeNull();
    // The other consumers of the same retained frame, asserted together: one of them left behind
    // is the defect over again, one consumer along.
    expect(result.current.aggregateReleaseSetId).toBeNull();
    expect(result.current.richnessGeoJSON).toBeNull();
    expect(result.current.effortGeoJSON).toBeNull();
    expect(result.current.laneReport.resultState).toBeUndefined();
    expect(result.current.laneReport.resultNote).toBeNull();
    expect(result.current.laneReport.truncated).toBe(false);
  });

  /**
   * The collapsed-container case (style review W10, B1) -- the conjunct the third fix missed.
   *
   * Nothing about the reader's INTENT changes here: the zoom is still 6, richness is still on, the
   * caller's toggle gate is still true. What changes is that the map container collapses or is
   * hidden, so `viewportBbox` can no longer express a viewport (`src/lib/map/viewport-bbox.ts:57-67`
   * returns null for a zero-size container) and the observer's `requested !== null` conjunct goes
   * false. The observer is disabled; `keepPreviousData` keeps serving the frame it landed before
   * the collapse. Every consumer of that frame would then be describing a viewport that does not
   * exist -- and the pin would claim a generation for cells nobody can see.
   */
  it("withholds the pin when the viewport collapses under a live aggregate toggle", () => {
    lane.band = "aggregate";
    lane.trpcQuery = {
      data: { ...aggregateAnswer("release-aggregate"), truncated: true, note: "capped at 5000 cells" },
      isError: true,
      isFetching: false,
      isPlaceholderData: true,
      isSuccess: true,
    };
    const { result, rerender } = renderHook(
      (props: { bbox: string | null }) =>
        useBotanicalViewportLanes({ ...BASE_OPTIONS, zoom: 6, richnessVisible: true, bbox: props.bbox }),
      { initialProps: { bbox: BASE_OPTIONS.bbox as string | null } }
    );
    expect(pinnedReleaseSetId()).toBe("release-aggregate");

    rerender({ bbox: null });

    expect(pinnedReleaseSetId()).toBeNull();
    expect(useBotanicalOccurrenceStore.getState().lastResponse).toBeNull();
    // Every other consumer of the same retained frame, asserted in the same case: one left behind
    // is the defect over again, one consumer along.
    expect(result.current.aggregateReleaseSetId).toBeNull();
    expect(result.current.richnessGeoJSON).toBeNull();
    expect(result.current.effortGeoJSON).toBeNull();
    expect(result.current.laneReport.isAggregateReadLive).toBe(false);
    expect(result.current.laneReport.resultState).toBeUndefined();
    expect(result.current.laneReport.resultNote).toBeNull();
    expect(result.current.laneReport.truncated).toBe(false);
    expect(result.current.laneReport.isError).toBe(false);
  });

  it("reports no aggregate-lane state at the detail band, retained answer or not", () => {
    lane.band = "detail";
    lane.trpcQuery = {
      data: aggregateAnswer("release-aggregate"),
      isError: true,
      isFetching: false,
      isPlaceholderData: true,
      isSuccess: false,
    };
    const { result } = renderHook(() =>
      useBotanicalViewportLanes({ ...BASE_OPTIONS, occurrencesVisible: true })
    );

    // Every tRPC-sourced field on the lane report is band-scoped, so none of the aggregate-band
    // fault entries can fire off an answer this band never read.
    expect(result.current.laneReport.resultState).toBeUndefined();
    expect(result.current.laneReport.isError).toBe(false);
    expect(result.current.laneReport.truncated).toBe(false);
    expect(result.current.aggregateReleaseSetId).toBeNull();
  });
});
