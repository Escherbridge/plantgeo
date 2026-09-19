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

vi.mock("@/hooks/useViewportProxiedLayers", () => ({
  botanicalBandForZoom: () => lane.band,
  useBotanicalOccurrencesQuery: (_bbox: unknown, options: Record<string, unknown>) => {
    lane.trpcCalls.push(options);
    return lane.trpcQuery;
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
