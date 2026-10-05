import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { unstable_localLink } from "@trpc/client";
import superjson from "superjson";
import type { Map as MapLibreMap } from "maplibre-gl";

/**
 * The window distribution flow: a tap on a dated scalar cell -> HoverTooltip -> the real
 * `layerWindow.distributionAtPoint` procedure -> the agri bridge, with only the HTTP edge
 * (`fetchBoundedJson`) and the Redis cache faked. See map/AGENTS.md §window-distribution.
 */
const edge = vi.hoisted(() => ({ cache: new Map<string, unknown>() }));
vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: async () => null }));
vi.mock("@/lib/server/redis", () => ({
  getCachedGeoJSON: async (key: string) => edge.cache.get(key) ?? null,
  cacheGeoJSON: async (key: string, value: unknown) => {
    edge.cache.set(key, value);
  },
}));
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>()),
  fetchBoundedJson: vi.fn(),
}));

import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";
import HoverTooltip from "@/components/map/HoverTooltip";
import { router } from "@/lib/server/trpc/init";
import { layerWindowRouter } from "@/lib/server/trpc/routers/layer-window";
import { trpc } from "@/lib/trpc/client";
import { useLayerWindowStore } from "@/stores/layer-window-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";

const fetchJson = vi.mocked(fetchBoundedJson);
const TODAY = "2026-10-04";
const testRouter = router({ layerWindow: layerWindowRouter });

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: TODAY,
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [
    {
      layerName: "vegetation",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2022-01-01",
      latestObservedDate: "2026-09-28",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
  ],
};

type Lane = Record<string, unknown>;

function lane(overrides: Lane = {}): Lane {
  return {
    parquet_lane: "vegetation_ndvi",
    signal_name: "ndvi",
    unit: null,
    days_in_window: 30,
    days_with_data: 28,
    stats: { min: 0.21, p10: 0.3, median: 0.36, p90: 0.41, max: 0.5, mean: 0.355 },
    spatial_relation: "covers",
    distance_km: null,
    distance_km_basis: null,
    static: false,
    state: "published",
    ...overrides,
  };
}

function agriAnswers(lanes: Lane[]) {
  fetchJson.mockImplementation(async (_url, init) => {
    const { arguments: args } = JSON.parse(String((init as RequestInit).body));
    return {
      tool: "distribution_at_point",
      result: { surface: args.surface_name, range_start: args.range_start, range_end: args.range_end, lanes },
    };
  });
}

function createFakeMap() {
  const listeners = new Map<string, Set<(event: unknown) => void>>();
  return {
    on: (type: string, handler: (event: unknown) => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
    },
    off: (type: string, handler: (event: unknown) => void) => listeners.get(type)?.delete(handler),
    emit: (type: string, event: unknown) => listeners.get(type)?.forEach((handler) => handler(event)),
    getStyle: () => ({ layers: [] }),
    getLayer: () => ({}),
    queryRenderedFeatures: (_geometry: unknown, options: { layers: string[] }) =>
      options.layers.includes("vegetation-ndvi-cells-fill")
        ? [{ layer: { id: "vegetation-ndvi-cells-fill" }, properties: { ndvi: 0.38, observedDay: "2026-09-28" } }]
        : [],
    getCanvas: () => ({ style: { cursor: "" } }),
    getContainer: () => ({ clientWidth: 800, clientHeight: 600 }),
  };
}

async function tapVegetationCell() {
  const fakeMap = createFakeMap();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const trpcClient = trpc.createClient({
    links: [
      unstable_localLink({
        router: testRouter,
        createContext: async () => ({ db: {}, session: null }) as never,
        transformer: superjson,
      }) as never,
    ],
  });
  render(
    <trpc.Provider client={trpcClient} queryClient={queryClient}>
      <QueryClientProvider client={queryClient}>
        <HoverTooltip map={fakeMap as unknown as MapLibreMap} />
      </QueryClientProvider>
    </trpc.Provider>
  );
  await act(async () => {
    fakeMap.emit("click", { point: { x: 100, y: 100 }, lngLat: { lng: -116.123456, lat: 43.654321 } });
  });
}

beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia;
  vi.stubEnv("AGRI_PARQUET_SERVICE_URL", "http://agri.internal:8000");
  edge.cache.clear();
  fetchJson.mockReset();
  useTimeSliderStore.setState({ capabilities: CAPABILITIES, layerDates: {} });
  useLayerWindowStore.setState({ layerWindowPresets: {} });
});

afterEach(() => {
  vi.unstubAllEnvs();
  // @ts-expect-error -- jsdom has no matchMedia; undo the per-test stub.
  delete window.matchMedia;
});

describe("the tapped cell's window distribution", () => {
  it("asks agri for the layer's own trailing 30-day window at the point rounded to 4 dp", async () => {
    agriAnswers([lane()]);
    await tapVegetationCell();

    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain(
      "30 d: median 0.36 (p10 0.30 – p90 0.41) · 28 of 30 days"
    );
    expect(String(fetchJson.mock.calls[0][0])).toBe("http://agri.internal:8000/api/v1/agent-tools/call");
    expect(JSON.parse(String((fetchJson.mock.calls[0][1] as RequestInit).body))).toEqual({
      name: "distribution_at_point",
      arguments: {
        surface_name: "vegetation",
        longitude: -116.1235,
        latitude: 43.6543,
        range_start: "2026-08-30",
        range_end: "2026-09-28",
      },
    });
  });

  it("follows the layer's chip preset and never asks past today", async () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: "2026-10-06" } });
    useLayerWindowStore.getState().setLayerWindowPreset("vegetation", 7);
    agriAnswers([lane({ days_in_window: 7, days_with_data: 6 })]);
    await tapVegetationCell();

    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain("7 d: median 0.36");
    const { arguments: args } = JSON.parse(String((fetchJson.mock.calls[0][1] as RequestInit).body));
    expect([args.range_start, args.range_end]).toEqual(["2026-09-28", TODAY]);
  });

  it("notes the nearest cell when no cell covers the point", async () => {
    agriAnswers([lane({ spatial_relation: "nearest_cell", distance_km: 23.64, distance_km_basis: "cell_edge" })]);
    await tapVegetationCell();

    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain(
      "28 of 30 days · nearest cell 23.6 km"
    );
  });

  it.each([
    ["a static lane", lane({ static: true, state: "static_not_applicable", stats: null })],
    ["a point outside every sparse area", lane({ spatial_relation: "nearest_area_outside", distance_km: 121.4 })],
    ["a refused lane", lane({ state: "refused", stats: null })],
  ])("shows nothing for %s", async (_case, answer) => {
    agriAnswers([answer]);
    await tapVegetationCell();

    await waitFor(() => expect(fetchJson).toHaveBeenCalledTimes(1));
    // The caption itself still renders; only the distribution line is withheld.
    expect(screen.getByText("Measured vegetation cell")).not.toBeNull();
    await act(async () => {});
    expect(screen.queryByTestId("layer-window-distribution")).toBeNull();
  });
});

describe("layerWindow.distributionAtPoint", () => {
  const caller = () => testRouter.createCaller({ db: {}, session: null } as never);
  const valid = { layerId: "vegetation", longitude: -116.2, latitude: 43.6, rangeStart: "2026-09-01", rangeEnd: "2026-09-30" };

  it("serves a repeated (layer, point, window) from the cache without a second agri call", async () => {
    agriAnswers([lane()]);
    const first = await caller().layerWindow.distributionAtPoint({ ...valid, longitude: -116.20001 });
    const second = await caller().layerWindow.distributionAtPoint({ ...valid, longitude: -116.20004 });

    expect(second).toEqual(first);
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["end before start", { rangeStart: "2026-09-30", rangeEnd: "2026-09-01" }],
    ["more than 366 days", { rangeStart: "2025-09-29", rangeEnd: "2026-09-30" }],
    ["an impossible day", { rangeStart: "2026-02-30" }],
    ["a layer with no stream", { layerId: "soil-soc" }],
    ["an unknown layer", { layerId: "not-a-layer" }],
  ])("rejects %s before reaching agri", async (_case, override) => {
    await expect(caller().layerWindow.distributionAtPoint({ ...valid, ...override })).rejects.toThrow();
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it("reports a contract mismatch as a retryable outage, not a line", async () => {
    fetchJson.mockResolvedValue({ tool: "distribution_at_point", result: { surface: "vegetation", lanes: "nope" } });
    await expect(caller().layerWindow.distributionAtPoint(valid)).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });
  });
});
