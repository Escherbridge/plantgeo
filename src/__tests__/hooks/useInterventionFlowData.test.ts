import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

/**
 * The viewport is stubbed rather than driven through the real map store, matching
 * `useParquetFireDetections.test.ts`'s convention: what is under test is that this hook debounces
 * the raw viewport before it reaches the query key, and gates the request on mode + a parseable
 * bbox -- not how a viewport bbox string is computed.
 */
const inputs = vi.hoisted(() => ({
  viewport: { zoom: 8, bbox: "-117,43,-116,44" } as { zoom: number; bbox: string | null },
  useQuery: vi.fn(),
}));

vi.mock("@/hooks/useViewportProxiedLayers", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useViewportProxiedLayers")>();
  return {
    ...actual,
    useViewportBounds: () => inputs.viewport,
  };
});

vi.mock("@/lib/trpc/client", () => ({
  trpc: { visualization: { getFlowData: { useQuery: inputs.useQuery } } },
}));

import { useInterventionFlowData } from "@/components/map/visualization/useInterventionFlowData";

function queryResult(overrides: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, ...overrides };
}

function lastCall(): [Record<string, unknown>, Record<string, unknown>] {
  const call = inputs.useQuery.mock.calls.at(-1);
  return [call?.[0] as Record<string, unknown>, call?.[1] as Record<string, unknown>];
}

beforeEach(() => {
  vi.useFakeTimers();
  inputs.viewport = { zoom: 8, bbox: "-117,43,-116,44" };
  inputs.useQuery.mockReturnValue(queryResult());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("useInterventionFlowData request", () => {
  it("asks for nothing while the mode is not 'flow'", () => {
    renderHook(() => useInterventionFlowData("off"));
    act(() => vi.advanceTimersByTime(1_000));

    expect(lastCall()[1].enabled).toBe(false);
  });

  it("sends the debounced bbox, the interventions layer name and the capped limit once settled", () => {
    renderHook(() => useInterventionFlowData("flow"));
    act(() => vi.advanceTimersByTime(400));

    const [input, options] = lastCall();
    expect(input).toEqual({
      layerName: "interventions",
      bbox: [-117, 43, -116, 44],
      limit: 500,
    });
    expect(options.enabled).toBe(true);
  });

  it("asks for nothing before the viewport can name a parseable bbox", () => {
    inputs.viewport = { zoom: 8, bbox: null };
    renderHook(() => useInterventionFlowData("flow"));
    act(() => vi.advanceTimersByTime(400));

    expect(lastCall()[1].enabled).toBe(false);
  });

  it("coalesces a burst of moveend-driven viewport changes into one settled request", () => {
    const { rerender } = renderHook(({ mode }) => useInterventionFlowData(mode), {
      initialProps: { mode: "flow" as const },
    });
    act(() => vi.advanceTimersByTime(400));
    expect(lastCall()[0].bbox).toEqual([-117, 43, -116, 44]);

    // Three rapid moveend-equivalent changes inside the debounce window -- a flyTo's
    // intermediate stops, or a fast pinch-pan -- must settle on the LAST one, not fire three
    // requests.
    inputs.viewport = { zoom: 8, bbox: "-117.1,43,-116,44" };
    rerender({ mode: "flow" });
    act(() => vi.advanceTimersByTime(100));
    inputs.viewport = { zoom: 8, bbox: "-117.2,43,-116,44" };
    rerender({ mode: "flow" });
    act(() => vi.advanceTimersByTime(100));
    inputs.viewport = { zoom: 8, bbox: "-117.3,43,-116,44" };
    rerender({ mode: "flow" });

    // Still mid-burst: the debounce has not settled, so the query must still see the FIRST bbox.
    act(() => vi.advanceTimersByTime(100));
    expect(lastCall()[0].bbox).toEqual([-117, 43, -116, 44]);

    // Settles 400ms after the LAST change.
    act(() => vi.advanceTimersByTime(300));
    expect(lastCall()[0].bbox).toEqual([-117.3, 43, -116, 44]);
  });
});

describe("useInterventionFlowData answer", () => {
  it("reports edges only while actually asking, and flags a response landing on the cap as possibly truncated", () => {
    const edges = Array.from({ length: 500 }, (_, index) => ({
      source: [-117 + index * 0.001, 43] as [number, number],
      target: [-116 + index * 0.001, 44] as [number, number],
      value: 1,
    }));
    inputs.useQuery.mockReturnValue(queryResult({ data: edges }));

    const { result } = renderHook(() => useInterventionFlowData("flow"));
    act(() => vi.advanceTimersByTime(400));

    expect(result.current.edges).toHaveLength(500);
    expect(result.current.isPossiblyTruncated).toBe(true);
  });

  it("withholds the previous answer once the mode switches away from flow", () => {
    inputs.useQuery.mockReturnValue(queryResult({ data: [{ source: [-117, 43], target: [-116, 44], value: 2 }] }));
    const { result, rerender } = renderHook(({ mode }) => useInterventionFlowData(mode), {
      initialProps: { mode: "flow" as const },
    });
    act(() => vi.advanceTimersByTime(400));
    expect(result.current.edges).toHaveLength(1);

    rerender({ mode: "points" });
    expect(result.current.edges).toHaveLength(0);
  });
});
