import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { unstable_localLink } from "@trpc/client";
import superjson from "superjson";
import type { Map as MapLibreMap } from "maplibre-gl";

/**
 * The window distribution flow: a tap or hover on a dated scalar cell -> HoverTooltip -> the real
 * `layerWindow.distributionAtPoint` procedure (rate limit, canonical window, Redis cache and
 * single-flight lock) -> the agri bridge. Faked: only the HTTP edge (`fetchBoundedJson`) and the
 * Redis wire (`ioredis-mock`, an in-memory Redis). See map/AGENTS.md §window-distribution.
 */
vi.mock("ioredis", async () => ({ default: (await import("ioredis-mock")).default }));
vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: async () => null }));
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>()),
  fetchBoundedJson: vi.fn(),
}));

import { fetchBoundedJson, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import HoverTooltip from "@/components/map/HoverTooltip";
import { getRedis } from "@/lib/server/redis";
import { router } from "@/lib/server/trpc/init";
import { LAYER_WINDOW_DISTRIBUTION_RATE_LIMIT, layerWindowRouter } from "@/lib/server/trpc/routers/layer-window";
import { trpc } from "@/lib/trpc/client";
import { useLayerWindowStore } from "@/stores/layer-window-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { SLIDER_STREAM_LAYER_NAMES } from "@/types/time-slider";
import type { SliderCapabilities } from "@/types/time-slider";
import { DEFAULT_SOIL_FIELD_DEPTHS } from "@/lib/environmental/soil-field";
import { useSoilStore } from "@/stores/soil-store";

const fetchJson = vi.mocked(fetchBoundedJson);
const TODAY = "2026-10-04";
const testRouter = router({ layerWindow: layerWindowRouter });

/** A browser request as the fetch adapter hands it over; the address is what the rate limit counts. */
function clientRequest(address = "203.0.113.7"): Request {
  return new Request("http://localhost/api/trpc/layerWindow.distributionAtPoint", {
    headers: { "x-forwarded-for": address },
  });
}

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
    {
      layerName: SLIDER_STREAM_LAYER_NAMES.soilMoisture,
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2022-04-30",
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

function agriAnswers(lanes: Lane[], delayMs = 0) {
  fetchJson.mockImplementation(async (_url, init) => {
    const { arguments: args } = JSON.parse(String((init as RequestInit).body));
    if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
    return {
      tool: "distribution_at_point",
      result: { surface: args.surface_name, range_start: args.range_start, range_end: args.range_end, lanes },
    };
  });
}

function agriBodies(): Array<{ name: string; arguments: Record<string, unknown> }> {
  return fetchJson.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
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
        ? [
            {
              layer: { id: "vegetation-ndvi-cells-fill" },
              properties: { ndvi: 0.38, observedDay: "2026-09-28", cellId: "ndvi-cell-1" },
            },
          ]
        : [],
    getCanvas: () => ({ style: { cursor: "" } }),
    getContainer: () => ({ clientWidth: 800, clientHeight: 600 }),
  };
}

function renderTooltip() {
  const fakeMap = createFakeMap();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const trpcClient = trpc.createClient({
    links: [
      unstable_localLink({
        router: testRouter,
        createContext: async () => ({ db: {}, session: null, req: clientRequest() }) as never,
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
  return fakeMap;
}

async function tapVegetationCell() {
  const fakeMap = renderTooltip();
  await act(async () => {
    fakeMap.emit("click", { point: { x: 100, y: 100 }, lngLat: { lng: -116.123456, lat: 43.654321 } });
  });
}

/** One ERA5-Land soil-moisture depth lane, as agri's `distribution_at_point` shapes it. */
function soilMoistureLane(depthLayer: 1 | 2 | 3, median: number): Lane {
  return {
    parquet_lane: "soil-field-moisture",
    signal_name: `soil_water_content_layer_${depthLayer}`,
    unit: "m^3/m^3",
    days_in_window: 30,
    days_with_data: 28,
    stats: {
      min: median - 0.05,
      p10: median - 0.03,
      median,
      p90: median + 0.03,
      max: median + 0.05,
      mean: median,
    },
    spatial_relation: "covers",
    distance_km: null,
    distance_km_basis: null,
    static: false,
    state: "published",
  };
}

/** A hovered `soil-moisture-field-fill` feature, carrying exactly what `getParquetSoilField`
 * puts on a `SoilFieldFeatureProperties` -- no `depth`, which is why the depth in the caption
 * below has to come from `soil-store.fieldDepth`, not the feature. */
function createSoilFakeMap(properties: Record<string, unknown>) {
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
      options.layers.includes("soil-moisture-field-fill")
        ? [{ layer: { id: "soil-moisture-field-fill" }, properties }]
        : [],
    getCanvas: () => ({ style: { cursor: "" } }),
    getContainer: () => ({ clientWidth: 800, clientHeight: 600 }),
  };
}

function renderSoilTooltip(properties: Record<string, unknown>) {
  const fakeMap = createSoilFakeMap(properties);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const trpcClient = trpc.createClient({
    links: [
      unstable_localLink({
        router: testRouter,
        createContext: async () => ({ db: {}, session: null, req: clientRequest() }) as never,
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
  return fakeMap;
}

function useCoarsePointer(coarse: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: coarse }) as unknown as typeof window.matchMedia;
}

beforeEach(async () => {
  useCoarsePointer(true);
  vi.stubEnv("AGRI_PARQUET_SERVICE_URL", "http://agri.internal:8000");
  await getRedis().flushall();
  fetchJson.mockReset();
  useTimeSliderStore.setState({ capabilities: CAPABILITIES, layerDates: {} });
  useLayerWindowStore.setState({ layerWindowPresets: {} });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
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
    expect(agriBodies()[0]).toEqual({
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
    const { arguments: args } = agriBodies()[0];
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
    ["a refused lane", lane({ state: "refused", stats: null, refusal_code: "serving_at_capacity" })],
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

describe("the hovered cell's window distribution", () => {
  it("asks once per cell: a second move inside the same cell keeps the line and its first point", async () => {
    useCoarsePointer(false);
    agriAnswers([lane()]);
    const fakeMap = renderTooltip();
    await act(async () => {
      fakeMap.emit("mousemove", { point: { x: 100, y: 100 }, lngLat: { lng: -116.123456, lat: 43.654321 } });
    });
    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain("median 0.36");

    await act(async () => {
      fakeMap.emit("mousemove", { point: { x: 104, y: 103 }, lngLat: { lng: -116.1291, lat: 43.6588 } });
      await new Promise((resolve) => setTimeout(resolve, 600)); // past the 400 ms hover settle
    });

    expect(screen.getByTestId("layer-window-distribution").textContent).toContain("median 0.36");
    expect(fetchJson).toHaveBeenCalledTimes(1);
    expect(agriBodies()[0].arguments).toMatchObject({ longitude: -116.1235, latitude: 43.6543 });
  });
});

describe("layerWindow.distributionAtPoint", () => {
  const caller = (address?: string) =>
    testRouter.createCaller({ db: {}, session: null, req: clientRequest(address) } as never);
  const valid = { layerId: "vegetation", longitude: -116.2, latitude: 43.6, rangeStart: "2026-09-01", rangeEnd: "2026-09-30" };

  it("serves a repeated (layer, point, window) from the cache without a second agri call", async () => {
    agriAnswers([lane()]);
    const first = await caller().layerWindow.distributionAtPoint({ ...valid, longitude: -116.20001 });
    const second = await caller().layerWindow.distributionAtPoint({ ...valid, longitude: -116.20004 });

    expect(second).toEqual(first);
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it("shares one agri call between identical concurrent misses", async () => {
    agriAnswers([lane()], 150);
    const [first, second, third] = await Promise.all([
      caller("198.51.100.1").layerWindow.distributionAtPoint(valid),
      caller("198.51.100.2").layerWindow.distributionAtPoint(valid),
      caller("198.51.100.3").layerWindow.distributionAtPoint(valid),
    ]);

    expect(fetchJson).toHaveBeenCalledTimes(1);
    expect(second).toEqual(first);
    expect(third).toEqual(first);
  });

  it(`refuses a client's call past ${LAYER_WINDOW_DISTRIBUTION_RATE_LIMIT} a minute, and only that client's`, async () => {
    // Mid-minute and frozen, so the limiter's fixed one-minute window cannot roll over mid-test.
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-04T12:00:30Z") });
    try {
      agriAnswers([lane()]);
      for (let call = 0; call < LAYER_WINDOW_DISTRIBUTION_RATE_LIMIT; call += 1) {
        await caller().layerWindow.distributionAtPoint(valid);
      }

      await expect(caller().layerWindow.distributionAtPoint(valid)).rejects.toMatchObject({
        code: "TOO_MANY_REQUESTS",
      });
      await expect(caller("192.0.2.44").layerWindow.distributionAtPoint(valid)).resolves.toMatchObject({
        surface: "vegetation",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("caches an answer with a transient lane refusal for 30 s and a settled one for 15 minutes", async () => {
    agriAnswers([lane({ state: "refused", stats: null, refusal_code: "serving_at_capacity" })]);
    await caller().layerWindow.distributionAtPoint(valid);
    agriAnswers([lane({ state: "refused", stats: null, refusal_code: "release_lane_not_distributed" })]);
    await caller().layerWindow.distributionAtPoint({ ...valid, longitude: -116.3 });

    const redis = getRedis();
    const answerKey = async (longitude: string) =>
      (await redis.keys(`layer-window-distribution:v2:*:${longitude}:*`)).find((key) => !key.endsWith(":lock"))!;
    const transientKey = await answerKey("-116.2000");
    const settledKey = await answerKey("-116.3000");
    expect(await redis.ttl(transientKey)).toBeLessThanOrEqual(30);
    expect(await redis.ttl(settledKey)).toBeGreaterThan(14 * 60);
  });

  it("remembers an agri timeout for a minute, so the next hover does not re-run the read", async () => {
    fetchJson.mockRejectedValue(new UpstreamTimeoutError("agri timed out"));
    await expect(caller().layerWindow.distributionAtPoint(valid)).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });
    await expect(caller().layerWindow.distributionAtPoint(valid)).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });

    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it("asks agri for one signal of a multi-signal surface", async () => {
    agriAnswers([lane({ parquet_lane: "weather-observations", signal_name: "air_temperature" })]);
    await caller().layerWindow.distributionAtPoint({ ...valid, layerId: "weather", signalName: "air_temperature" });

    expect(agriBodies()[0].arguments).toMatchObject({ surface_name: "weather-observations", signal_name: "air_temperature" });
  });

  it.each([
    ["end before start", { rangeStart: "2026-09-30", rangeEnd: "2026-09-01" }],
    ["more than 366 days", { rangeStart: "2025-09-29", rangeEnd: "2026-09-30" }],
    ["a length that is not a preset", { rangeStart: "2026-09-02" }],
    ["an end after today (UTC)", { rangeStart: "2099-01-01", rangeEnd: "2099-01-30" }],
    ["an impossible day", { rangeStart: "2026-02-30" }],
    ["a layer with no stream", { layerId: "soil-soc" }],
    ["an unknown layer", { layerId: "not-a-layer" }],
    ["a signal the layer does not show", { signalName: "air_temperature" }],
  ])("rejects %s before reaching agri", async (_case, override) => {
    await expect(caller().layerWindow.distributionAtPoint({ ...valid, ...override })).rejects.toMatchObject({
      code: "BAD_REQUEST",
    });
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it("reports a contract mismatch as a retryable outage, not a line", async () => {
    fetchJson.mockResolvedValue({ tool: "distribution_at_point", result: { surface: "vegetation", lanes: "nope" } });
    await expect(caller().layerWindow.distributionAtPoint(valid)).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });
  });
});

/**
 * `hover-fields.ts` previously had no `soil-moisture-field-fill` / `soil-temperature-field-fill`
 * in `HOVERABLE_LAYER_IDS`, so a hover over a soil field reached no `formatHoverContent` entry,
 * `HoverTooltip` never rendered a tooltip, and `WindowDistributionLine` -- mounted only inside
 * that tooltip -- never got the chance to ask for a distribution at all, even though the query it
 * would send was already correct (`soil-window-distribution.test.tsx` renders
 * `WindowDistributionLine` directly and was green the whole time). This proves the full path a
 * real hover takes: `mousemove` -> caption with the painted value and depth -> the distribution
 * line asking for THAT depth's `signal_name`, not the default.
 */
describe("the hovered soil-moisture cell's window distribution", () => {
  it("shows the painted value and wires the distribution line to the painted depth, not the default", async () => {
    useCoarsePointer(false);
    useSoilStore.setState({ fieldDepth: { ...DEFAULT_SOIL_FIELD_DEPTHS, moisture: "root-zone" } });
    agriAnswers([soilMoistureLane(1, 0.08), soilMoistureLane(2, 0.22), soilMoistureLane(3, 0.34)]);
    const fakeMap = renderSoilTooltip({
      value: 0.214,
      aggregated: false,
      coverageFraction: 0.92,
      cellKey: "z9:12:34",
    });

    await act(async () => {
      fakeMap.emit("mousemove", { point: { x: 100, y: 100 }, lngLat: { lng: -116.123456, lat: 43.654321 } });
    });

    // `getByText` throws (failing the test) if the caption is missing; no further assertion needed.
    screen.getByText("Volumetric soil water");
    screen.getByText("Value: 0.214 m³/m³");
    screen.getByText("Depth: Root zone (7-28 cm)");
    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain("median 0.22");
    expect(agriBodies()[0].arguments).toMatchObject({
      surface_name: "soil-field-moisture",
      signal_name: "soil_water_content_layer_2",
    });
  });
});
