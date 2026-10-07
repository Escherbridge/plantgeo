import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { unstable_localLink } from "@trpc/client";
import superjson from "superjson";

/**
 * The soil half of the window-distribution flow: a painted depth (`soil-store.fieldDepth`) must
 * reach agri as the one `signal_name` for that depth, and the lane agri answers with must be the
 * one actually displayed -- not `lanes[0]` of whatever order the server happens to return. Faked:
 * only the HTTP edge (`fetchBoundedJson`) and the Redis wire (`ioredis-mock`), same as
 * `window-distribution.test.tsx`. See src/lib/server/services/AGENTS.md §window-distribution.
 */
vi.mock("ioredis", async () => ({ default: (await import("ioredis-mock")).default }));
vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: async () => null }));
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>()),
  fetchBoundedJson: vi.fn(),
}));

import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";
import { WindowDistributionLine } from "@/components/map/WindowDistributionLine";
import { getRedis } from "@/lib/server/redis";
import { router } from "@/lib/server/trpc/init";
import { layerWindowRouter } from "@/lib/server/trpc/routers/layer-window";
import { trpc } from "@/lib/trpc/client";
import { useLayerWindowStore } from "@/stores/layer-window-store";
import { DEFAULT_SOIL_FIELD_DEPTHS } from "@/lib/environmental/soil-field";
import { useSoilStore } from "@/stores/soil-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { SLIDER_STREAM_LAYER_NAMES } from "@/types/time-slider";
import type { SliderCapabilities } from "@/types/time-slider";

const fetchJson = vi.mocked(fetchBoundedJson);
const TODAY = "2026-10-04";
const testRouter = router({ layerWindow: layerWindowRouter });

function clientRequest(): Request {
  return new Request("http://localhost/api/trpc/layerWindow.distributionAtPoint", {
    headers: { "x-forwarded-for": "203.0.113.9" },
  });
}

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: TODAY,
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [
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
    {
      layerName: SLIDER_STREAM_LAYER_NAMES.soilTemperature,
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

/** One ERA5-Land soil-moisture depth lane, as agri's `distribution_at_point` shapes it. */
function moistureLane(depth: 1 | 2 | 3, median: number) {
  return {
    parquet_lane: "soil-field-moisture",
    signal_name: `soil_water_content_layer_${depth}`,
    unit: "m^3/m^3",
    days_in_window: 30,
    days_with_data: 28,
    stats: { min: median - 0.05, p10: median - 0.03, median, p90: median + 0.03, max: median + 0.05, mean: median },
    spatial_relation: "covers",
    distance_km: null,
    distance_km_basis: null,
    static: false,
    state: "published",
  };
}

/** Answers with every depth lane, root depth FIRST -- the exact shape `selectDistributionLane`
 * must not fall through to `lanes[0]` of, and the order the un-fixed bug actually returned. */
function agriAnswersAllDepths() {
  fetchJson.mockImplementation(async (_url, init) => {
    const { arguments: args } = JSON.parse(String((init as RequestInit).body));
    return {
      tool: "distribution_at_point",
      result: {
        surface: args.surface_name,
        range_start: args.range_start,
        range_end: args.range_end,
        lanes: [moistureLane(1, 0.08), moistureLane(2, 0.22), moistureLane(3, 0.34)],
      },
    };
  });
}

function agriBodies(): Array<{ name: string; arguments: Record<string, unknown> }> {
  return fetchJson.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

function renderSoilMoistureLine() {
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
        <WindowDistributionLine
          styleLayerId="soil-moisture-field-fill"
          lngLat={{ lng: -116.123456, lat: 43.654321 }}
          settleMs={0}
        />
      </QueryClientProvider>
    </trpc.Provider>
  );
}

beforeEach(async () => {
  vi.stubEnv("AGRI_PARQUET_SERVICE_URL", "http://agri.internal:8000");
  await getRedis().flushall();
  fetchJson.mockReset();
  useTimeSliderStore.setState({ capabilities: CAPABILITIES, layerDates: {} });
  useLayerWindowStore.setState({ layerWindowPresets: {} });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the painted soil depth's window distribution", () => {
  it("sends the painted depth's signal name and shows that depth's lane, not lanes[0]", async () => {
    useSoilStore.setState({ fieldDepth: { ...DEFAULT_SOIL_FIELD_DEPTHS, moisture: "root-zone" } });
    agriAnswersAllDepths();
    renderSoilMoistureLine();

    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain("median 0.22");
    expect(agriBodies()[0].arguments).toMatchObject({
      surface_name: "soil-field-moisture",
      signal_name: "soil_water_content_layer_2",
    });
  });

  it("asks for a different lane when the painted depth changes, and shows that one", async () => {
    useSoilStore.setState({ fieldDepth: { ...DEFAULT_SOIL_FIELD_DEPTHS, moisture: "deep" } });
    agriAnswersAllDepths();
    renderSoilMoistureLine();

    expect((await screen.findByTestId("layer-window-distribution")).textContent).toContain("median 0.34");
    expect(agriBodies()[0].arguments).toMatchObject({ signal_name: "soil_water_content_layer_3" });
  });
});

describe("layerWindow.distributionAtPoint for the soil toggles", () => {
  const caller = () => testRouter.createCaller({ db: {}, session: null, req: clientRequest() } as never);
  const valid = {
    layerId: "soil-moisture",
    longitude: -116.2,
    latitude: 43.6,
    rangeStart: "2026-09-01",
    rangeEnd: "2026-09-30",
  };

  it("accepts the exact depth signal distributionSignalName can send", async () => {
    agriAnswersAllDepths();
    await expect(
      caller().layerWindow.distributionAtPoint({ ...valid, signalName: "soil_water_content_layer_2" })
    ).resolves.toMatchObject({ surface: "soil-field-moisture" });
  });

  it("rejects the sibling measure's signal name before reaching agri", async () => {
    await expect(
      caller().layerWindow.distributionAtPoint({ ...valid, signalName: "soil_temperature_level_1" })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it("rejects a signal name that is not a real warehouse signal at all", async () => {
    await expect(
      caller().layerWindow.distributionAtPoint({ ...valid, signalName: "not_a_real_signal" })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(fetchJson).not.toHaveBeenCalled();
  });
});
