import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import type {
  ParquetFireDetectionCell,
  ParquetFireWindow,
  ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";

/**
 * The two key inputs are stubbed rather than driven through the map and slider stores: what is
 * under test is that the hook THREADS them into one query key and reads the answer's state
 * honestly, not how a viewport bbox is computed. Both stubs are mutable so a case can pan or
 * scrub between renders.
 */
const inputs = vi.hoisted(() => ({
  viewport: { zoom: 9, bbox: "-117,43,-116,44" } as { zoom: number; bbox: string | null },
  day: { settledDate: "2026-08-28" as string | null, requestDate: "2026-08-28" as string | undefined },
  useQuery: vi.fn(),
  /** Whether the registry withholds `fire` at every date; false in every case but one. */
  fireWithheld: false,
}));

vi.mock("@/lib/map/layer-registry", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/map/layer-registry")>();
  return {
    ...actual,
    isLayerPermanentlyWithheld: (toggleId: string) =>
      toggleId === "fire" ? inputs.fireWithheld : actual.isLayerPermanentlyWithheld(toggleId as never),
  };
});

vi.mock("@/hooks/useViewportProxiedLayers", () => ({
  useViewportBounds: () => inputs.viewport,
}));

vi.mock("@/lib/map/layer-toggle-context", () => ({
  useDebouncedLayerDay: () => ({
    settledDate: inputs.day.settledDate,
    serverCurrentDate: "2026-08-28",
    isOffServerToday: false,
    requestDate: inputs.day.requestDate,
  }),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: { wildfire: { getFireDetections: { useQuery: inputs.useQuery } } },
}));

import { useParquetFireDetections } from "@/hooks/useParquetFireDetections";

/** Everything the hook reads off a react-query result; flags default to a settled success. */
function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isSuccess: false,
    isError: false,
    isFetching: false,
    isPlaceholderData: false,
    ...overrides,
  };
}

/**
 * A cell as the reader serves it at the z9 rung. The envelope is what lets a RETAINED frame
 * keep saying z9 after the camera has crossed the z13 breakpoint: the cells in hand carry the
 * rung they were really aggregated at, and the hook's own latch covers only the tier it reports
 * for a caption.
 */
function cell(overrides: Partial<ParquetFireDetectionCell> = {}): ParquetFireDetectionCell {
  return {
    longitude: -116.25,
    latitude: 43.5,
    observedDay: "2026-08-28",
    detectionCount: 4,
    frpSum: 120.5,
    frpObservationCount: 4,
    highConfidenceDetectionCount: 2,
    newestObservedAt: "2026-08-28T19:12:00Z",
    support: {
      zoomTier: 9,
      supportKind: "aggregate_cell",
      // `mintedSupportId`'s real format -- the rung, then the position, unpadded and unprefixed.
      supportId: "9:-116.25:43.5",
      origin: "cell_origin",
      // The ladder's z9 grid, from DERIVED_TIER_CELL_DEGREES. Not 0.25, which is the vegetation
      // and soil-field base grain and has never been a fire cell size at any rung.
      cellWidthDegrees: 0.01,
      cellHeightDegrees: 0.01,
      cellOriginDegrees: [-116.25, 43.5],
      aggregationMethod: "count",
      contributorCount: 4,
      provenance: {
        sourceLayer: "fire_detections",
        observedDay: "2026-08-28",
        newestObservedAt: "2026-08-28T19:12:00Z",
        attribution: "NASA FIRMS",
      },
    },
    ...overrides,
  };
}

function readyWindow(
  cells: ParquetFireDetectionCell[],
  truncated = false
): ParquetReaderResult<ParquetFireWindow> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    truncated,
    data: { firstDay: "2026-08-28", lastDay: "2026-08-28", cells, days: [] },
  };
}

/** A landed answer: succeeded, nothing standing in for it, nothing in flight. */
function landed(data: ParquetReaderResult<ParquetFireWindow>) {
  return queryResult({ data, isSuccess: true, isPlaceholderData: false });
}

/** The next key is loading and the previous key's answer is still painted. */
function retaining(data: ParquetReaderResult<ParquetFireWindow>) {
  return queryResult({ data, isSuccess: true, isPlaceholderData: true, isFetching: true });
}

/** The input the hook last sent, and the options it sent them under. */
function lastCall(): [Record<string, unknown>, Record<string, unknown>] {
  const call = inputs.useQuery.mock.calls.at(-1);
  return [call?.[0] as Record<string, unknown>, call?.[1] as Record<string, unknown>];
}

beforeEach(() => {
  inputs.viewport = { zoom: 9, bbox: "-117,43,-116,44" };
  inputs.day = { settledDate: "2026-08-28", requestDate: "2026-08-28" };
  inputs.fireWithheld = false;
  inputs.useQuery.mockReturnValue(queryResult());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useParquetFireDetections request", () => {
  it("sends the fire row's day, the viewport bbox and the viewport zoom as one key", () => {
    renderHook(() => useParquetFireDetections(true));

    const [input] = lastCall();
    expect(input).toEqual({
      bbox: "-117,43,-116,44",
      date: "2026-08-28",
      zoom: 9,
      // One day, never the rolling lookback: the row's slider names exactly one.
      dayRange: 1,
    });
  });

  it("omits the date for a row sitting on the server's today, keeping one cache entry", () => {
    inputs.day = { settledDate: "2026-08-28", requestDate: undefined };
    renderHook(() => useParquetFireDetections(true));

    expect(lastCall()[0].date).toBeUndefined();
  });

  it("retains the previous answer while panning rather than blanking the layer", () => {
    renderHook(() => useParquetFireDetections(true));

    // The other half of this is the drawn-day report LayerManager publishes; retention without
    // a label is the pairing `src/components/map/AGENTS.md` forbids.
    expect(lastCall()[1].placeholderData).toBeTypeOf("function");
    expect(lastCall()[1].retry).toBe(1);
  });

  it("asks for nothing while the caller's gate is closed", () => {
    renderHook(() => useParquetFireDetections(false));

    expect(lastCall()[1].enabled).toBe(false);
  });

  it("asks for nothing before the viewport can name a bbox", () => {
    inputs.viewport = { zoom: 9, bbox: null };
    renderHook(() => useParquetFireDetections(true));

    expect(lastCall()[1].enabled).toBe(false);
  });

  it("asks for nothing at a zoom no published rung serves", () => {
    // `resolveZoomTier` raises on a non-finite zoom, and the reader would surface that as an
    // outage. A request that cannot be answered is not sent.
    inputs.viewport = { zoom: Number.NaN, bbox: "-117,43,-116,44" };
    renderHook(() => useParquetFireDetections(true));

    expect(lastCall()[1].enabled).toBe(false);
  });

  /**
   * Governance applied to the REQUEST, not only to the render -- the rule every proxied lane
   * already applies. This hook has two callers, the map layer and the dock panel, so without it
   * an open `FireDetails` keeps requesting a layer the map is forbidden to draw: a panel as the
   * sole requester of a withheld layer is exactly the case the rule exists for.
   */
  it("asks for nothing when the registry withholds the fire layer at every date", () => {
    inputs.fireWithheld = true;
    renderHook(() => useParquetFireDetections(true));

    expect(lastCall()[1].enabled).toBe(false);
  });

  it("asks normally while the layer is not withheld", () => {
    renderHook(() => useParquetFireDetections(true));

    expect(lastCall()[1].enabled).toBe(true);
  });
});

describe("useParquetFireDetections answer", () => {
  it("presents a served window as drawable cells with its own totals", () => {
    inputs.useQuery.mockReturnValue(
      landed(readyWindow([cell(), cell({ detectionCount: 11, highConfidenceDetectionCount: 0 })]))
    );
    const { result } = renderHook(() => useParquetFireDetections(true));

    expect(result.current.state).toBe("ready");
    expect(result.current.geojson.features).toHaveLength(2);
    expect(result.current.detectionCount).toBe(15);
    expect(result.current.cellCount).toBe(2);
    expect(result.current.truncated).toBe(false);
    expect(result.current.hasLandedForRequestedDate).toBe(true);
  });

  it("surfaces a truncated read instead of drawing the subset as the whole viewport", () => {
    inputs.useQuery.mockReturnValue(landed(readyWindow([cell()], true)));
    const { result } = renderHook(() => useParquetFireDetections(true));

    expect(result.current.truncated).toBe(true);
  });

  it("reports each terminal state as itself, never as a count of zero", () => {
    const terminals: [ParquetReaderResult<ParquetFireWindow>, string][] = [
      [
        {
          state: "absent",
          requestedDay: "2026-08-28",
          servedDay: "2026-08-28",
          evidence: {
            reason: "source_empty",
            upstreamResponse: "200 with no rows",
            recordedAt: "2026-08-28T02:00:00Z",
            runId: "run-1",
          },
        },
        "absent",
      ],
      [
        { state: "not_generated", requestedDay: "2026-08-28", reason: "lane_never_written" },
        "not_generated",
      ],
      [
        { state: "upstream_unavailable", fault: { kind: "network", message: "reset" } },
        "upstream_unavailable",
      ],
    ];

    for (const [data, expected] of terminals) {
      inputs.useQuery.mockReturnValue(landed(data));
      const { result, unmount } = renderHook(() => useParquetFireDetections(true));
      expect(result.current.state, expected).toBe(expected);
      expect(result.current.geojson.features, expected).toEqual([]);
      // The result itself is handed back so a surface can render the evidence/reason/fault
      // rather than a generic failure line.
      expect(result.current.result, expected).toBe(data);
      unmount();
    }
  });

  it("does not count an unreachable data service as a landed day", () => {
    inputs.useQuery.mockReturnValue(
      landed({ state: "upstream_unavailable", fault: { kind: "timeout", message: "slow" } })
    );
    const { result } = renderHook(() => useParquetFireDetections(true));

    // Recording a fault as landed poisons the drawn-day ledger: the NEXT request's retained
    // frame would be painted under the failed day's date.
    expect(result.current.hasLandedForRequestedDate).toBe(false);
  });

  it("tells a request that never returned a typed state apart from one that is still open", () => {
    const { result, rerender } = renderHook(() => useParquetFireDetections(true));
    expect(result.current.state).toBe("pending");

    inputs.useQuery.mockReturnValue(queryResult({ isError: true }));
    rerender();
    expect(result.current.state).toBe("request_failed");
    expect(result.current.hasLandedForRequestedDate).toBe(false);
  });
});

describe("useParquetFireDetections retained frames", () => {
  it("labels a retained frame rather than reporting it as this key's answer", () => {
    inputs.useQuery.mockReturnValue(retaining(readyWindow([cell()])));
    const { result } = renderHook(() => useParquetFireDetections(true));

    expect(result.current.isShowingPreviousDay).toBe(true);
    expect(result.current.hasLandedForRequestedDate).toBe(false);
    expect(result.current.isFetching).toBe(true);
    // The cells stay on the canvas -- retention is the point; what must not happen is the
    // caption calling them the requested day's own answer.
    expect(result.current.geojson.features).toHaveLength(1);
  });

  it("keeps a retained frame labelled with the rung it was actually aggregated at", () => {
    inputs.useQuery.mockReturnValue(landed(readyWindow([cell()])));
    const { result, rerender } = renderHook(() => useParquetFireDetections(true));
    expect(result.current.zoomTier).toBe(9);

    // The reader zooms in past the z13 breakpoint; the z13 request has not answered, so what is
    // painted is still the z9 cells. Labelling them z13 would state an aggregation nothing
    // performed -- the same misstatement the drawn-day latch exists to prevent for the day.
    inputs.viewport = { zoom: 14, bbox: "-116.6,43.4,-116.4,43.8" };
    inputs.useQuery.mockReturnValue(retaining(readyWindow([cell()])));
    rerender();

    expect(lastCall()[0].zoom).toBe(14);
    expect(result.current.zoomTier).toBe(9);
    expect(result.current.geojson.features[0].properties.zoomTier).toBe(9);

    // ...and moves to z13 only once a z13 answer lands.
    inputs.useQuery.mockReturnValue(landed(readyWindow([cell()])));
    rerender();
    expect(result.current.zoomTier).toBe(13);
  });
});
