import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ useViewedLayerDays: vi.fn() }));

vi.mock("@/lib/map/layer-toggle-context", () => ({
  useViewedLayerDays: mocks.useViewedLayerDays,
}));

import { useRegionalIntelligence } from "@/hooks/useRegionalIntelligence";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";

const FIRE_COVERAGE_GAP = { from: "2023-03-01", to: "2025-11-05" };

/**
 * The day fire-detections' capped coverage lists stop describing, and a day below it.
 *
 * Below the boundary a gap that was dropped by the cap is indistinguishable from continuous
 * coverage, so "not in coverageGaps" is not evidence of anything -- which is why the day below
 * must be posted as unaffirmed rather than as having data.
 */
const FIRE_DESCRIBED_FROM_DAY = "2023-02-01";
const DAY_BELOW_THE_DESCRIBED_BOUNDARY = "2023-01-15";

const capabilities: SliderCapabilities = {
  serverCurrentDate: "2026-08-09",
  futureAxisDays: 0,
  layers: [
    {
      layerName: "fire-detections",
      temporalKind: "event",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2023-01-01",
      latestObservedDate: "2026-08-08",
      coverageGaps: [FIRE_COVERAGE_GAP],
      thinRanges: [],
      describedFromDay: FIRE_DESCRIBED_FROM_DAY,
    },
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2024-01-01",
      latestObservedDate: "2026-08-08",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
  ],
};

/**
 * The request is refused at the status line, so the body is captured without any stream
 * plumbing. What is under test is what the client SENDS, not what it does with the answer.
 */
function refusingFetch() {
  return vi.fn().mockResolvedValue({
    ok: false,
    status: 503,
    json: async () => ({ error: "unavailable", retryable: false }),
  });
}

function postedBody(fetchMock: ReturnType<typeof refusingFetch>) {
  return JSON.parse(String(fetchMock.mock.calls[0][1].body)) as {
    viewedLayers?: Array<{ layer: string; date: string; hasDataOnDate: boolean }>;
  };
}

beforeEach(() => {
  vi.stubGlobal("crypto", {
    ...globalThis.crypto,
    randomUUID: () => "00000000-0000-4000-8000-000000000000",
  });
  useTimeSliderStore.setState({ capabilities });
  mocks.useViewedLayerDays.mockReturnValue([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("posting the days the user is viewing with an analysis request", () => {
  it("carries every visible layer's own day", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "fire",
        warehouseLayerName: "fire-detections",
        date: "2026-02-14",
        isOnLatest: false,
      },
      {
        layerId: "water",
        warehouseLayerName: "water-gauges",
        date: "2026-08-08",
        isOnLatest: true,
      },
    ]);

    const { result } = renderHook(() => useRegionalIntelligence());
    await waitFor(() => expect(mocks.useViewedLayerDays).toHaveBeenCalled());
    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    expect(postedBody(fetchMock).viewedLayers).toEqual([
      { layer: "fire", date: "2026-02-14", hasDataOnDate: true },
      { layer: "water", date: "2026-08-08", hasDataOnDate: true },
    ]);
  });

  it("reports a day inside the layer's coverage gap as having no data on that day", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "fire",
        warehouseLayerName: "fire-detections",
        date: "2024-06-01",
        isOnLatest: false,
      },
    ]);

    const { result } = renderHook(() => useRegionalIntelligence());
    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    expect(postedBody(fetchMock).viewedLayers).toEqual([
      { layer: "fire", date: "2024-06-01", hasDataOnDate: false },
    ]);
  });

  it("reports a layer no warehouse stream backs as unaffirmed rather than as having data", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    // The SoilGrids raster: drawn from raster tiles, so nothing in the capability payload can
    // ever speak for it. `drought` used to stand here and no longer can -- it is published as
    // the `drought-areas` stream and has a coverage record like every other dated layer.
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "soil",
        warehouseLayerName: null,
        date: "2026-01-07",
        isOnLatest: true,
      },
    ]);

    const { result } = renderHook(() => useRegionalIntelligence());
    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    expect(postedBody(fetchMock).viewedLayers).toEqual([
      { layer: "soil", date: "2026-01-07", hasDataOnDate: false },
    ]);
  });

  /**
   * F5's client half. A day below the reported boundary is outside every listed gap, which is
   * exactly what a published day looks like -- and the list is capped, so that shape is an
   * artefact rather than evidence. Posting `true` here made the server's own (correct) "coverage
   * unknown" and the client's claim disagree with nothing flagging it: `clientClaimContradicted`
   * is only computed on the published and not-published branches, so the unknown branch let the
   * disagreement through in silence.
   */
  it("reports a day the coverage lists no longer describe as unaffirmed, not as having data", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "fire",
        warehouseLayerName: "fire-detections",
        date: DAY_BELOW_THE_DESCRIBED_BOUNDARY,
        isOnLatest: false,
      },
    ]);

    const { result } = renderHook(() => useRegionalIntelligence());
    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    expect(postedBody(fetchMock).viewedLayers).toEqual([
      { layer: "fire", date: DAY_BELOW_THE_DESCRIBED_BOUNDARY, hasDataOnDate: false },
    ]);
  });

  it("still affirms a described day the lists report no gap on", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    // One day above the boundary and outside the gap. The fix must narrow what the client will
    // affirm, not stop it affirming anything.
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "fire",
        warehouseLayerName: "fire-detections",
        date: "2023-02-10",
        isOnLatest: false,
      },
    ]);

    const { result } = renderHook(() => useRegionalIntelligence());
    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    expect(postedBody(fetchMock).viewedLayers).toEqual([
      { layer: "fire", date: "2023-02-10", hasDataOnDate: true },
    ]);
  });

  it("omits the field entirely when no layer is visible, matching an older client", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    mocks.useViewedLayerDays.mockReturnValue([]);

    const { result } = renderHook(() => useRegionalIntelligence());
    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    const body = postedBody(fetchMock);
    expect(body.viewedLayers).toBeUndefined();
    expect("viewedLayers" in body).toBe(false);
  });

  it("sends the day the row is on when the request is made, not when the callback was built", async () => {
    const fetchMock = refusingFetch();
    vi.stubGlobal("fetch", fetchMock);
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "water",
        warehouseLayerName: "water-gauges",
        date: "2026-08-08",
        isOnLatest: true,
      },
    ]);

    const { result, rerender } = renderHook(() => useRegionalIntelligence());
    mocks.useViewedLayerDays.mockReturnValue([
      {
        layerId: "water",
        warehouseLayerName: "water-gauges",
        date: "2025-04-01",
        isOnLatest: false,
      },
    ]);
    rerender();

    await act(async () => {
      await result.current.queryLocation(43.6, -116.2);
    });

    expect(postedBody(fetchMock).viewedLayers).toEqual([
      { layer: "water", date: "2025-04-01", hasDataOnDate: true },
    ]);
  });
});
