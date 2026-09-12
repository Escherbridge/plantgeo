import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { WeatherHistoryReport, windDirectionLabel } from "@/components/panels/WeatherHistoryReport";
import { useMapStore } from "@/stores/map-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { renderWithProviders } from "@/test/utils";
import type {
  ParquetBrowserReaderResult,
  ParquetBrowserWeatherObservation,
} from "@/lib/environmental/parquet-presentation";

const DAY = "2026-08-01";

interface WeatherQueryResult {
  data?: ParquetBrowserReaderResult<readonly ParquetBrowserWeatherObservation[]>;
  isFetching?: boolean;
  isPlaceholderData?: boolean;
  isError?: boolean;
}

const queries = vi.hoisted(() => ({
  getWeatherForBbox: vi.fn((): WeatherQueryResult => ({ data: undefined })),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: { wildfire: { getWeatherForBbox: { useQuery: queries.getWeatherForBbox } } },
}));

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_TIME_STATE = useTimeSliderStore.getState();

const support = {
  zoomTier: 13 as const,
  supportKind: "raw_point" as const,
  supportId: "weather-sample",
  origin: "cell_center" as const,
  aggregationMethod: "none" as const,
  contributorCount: 1,
  provenance: {
    sourceLayer: "weather-observations",
    observedDay: DAY,
    newestObservedAt: `${DAY}T12:00:00Z`,
    attribution: "Open-Meteo",
  },
};

const observation: ParquetBrowserWeatherObservation = {
  latitude: 43.6,
  longitude: -116.2,
  observedAt: `${DAY}T12:00:00Z`,
  observedDay: DAY,
  externalId: "weather-sample",
  temperatureC: 21.5,
  relativeHumidityPct: 44,
  windSpeedMs: 3.2,
  windDirectionDeg: 270,
  precipitationMm: 0,
  source: "Open-Meteo",
  featureId: "weather-sample",
  ingestedAt: `${DAY}T12:05:00Z`,
  support,
};

beforeEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useTimeSliderStore.setState(INITIAL_TIME_STATE, true);
  useMapStore.setState({ viewport: { ...INITIAL_MAP_STATE.viewport, latitude: 43.6, longitude: -116.2 } });
  useTimeSliderStore.setState({
    layerDates: { weather: DAY },
    capabilities: {
      serverCurrentDate: "2026-09-11",
      futureAxisDays: 0,
      streamsUnavailable: false,
      layers: [{
        layerName: "weather-observations",
        temporalKind: "daily_series",
        forecastHorizonDays: 0,
        forecastVariants: [],
        earliestObservedDate: "2026-07-01",
        latestObservedDate: DAY,
        coverageGaps: [],
        thinRanges: [],
        describedFromDay: null,
      }],
    },
  });
  queries.getWeatherForBbox.mockReturnValue({ data: undefined });
});

afterEach(() => {
  vi.clearAllMocks();
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useTimeSliderStore.setState(INITIAL_TIME_STATE, true);
});

describe("WeatherHistoryReport", () => {
  it("presents the selected historical day in forecast-style cards without losing measured zero", () => {
    queries.getWeatherForBbox.mockReturnValue({
      data: { state: "ready", requestedDay: DAY, servedDay: DAY, truncated: false, data: [observation] },
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
    });

    renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(screen.getByText(`${DAY} · Open-Meteo historical estimates · SI units`)).toBeTruthy();
    expect(screen.getAllByText("21.5 °C").length).toBeGreaterThan(0);
    expect(screen.getByText("44% RH")).toBeTruthy();
    expect(screen.getByText("W (270°)")).toBeTruthy();
    expect(screen.getByText("0.0 mm")).toBeTruthy();
    expect(screen.getByText(/gaps are unmeasured, not interpolated/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Mark nearest weather reading on map" }));
    expect(useMapStore.getState().queryPoint).toEqual({ lat: 43.6, lon: -116.2 });
    expect(screen.getByText("Nearest to selected point")).toBeTruthy();
  });

  it("states an unavailable selected day and exposes no previous values", () => {
    queries.getWeatherForBbox.mockReturnValue({
      data: { state: "not_generated", requestedDay: DAY, reason: "day_not_written" },
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
    });

    renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(screen.getByText(`No weather data was published for ${DAY}. Nothing is drawn.`)).toBeTruthy();
    expect(screen.queryByText("21.5 °C")).toBeNull();
  });

  it("clears a displayed selected-day fixture when a typed upstream outage replaces it", () => {
    queries.getWeatherForBbox.mockReturnValue({
      data: { state: "ready", requestedDay: DAY, servedDay: DAY, truncated: false, data: [observation] },
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
    });

    const rendered = renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(screen.getAllByText("21.5 °C").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Mark nearest weather reading on map" })).toBeTruthy();

    queries.getWeatherForBbox.mockReturnValue({
      data: {
        state: "upstream_unavailable",
        fault: { kind: "http", message: "upstream 503", status: 503 },
      },
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
    });

    rendered.rerender(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(
      screen.getByText("Historical weather is temporarily unavailable from the data service. No fallback frame is shown.")
    ).toBeTruthy();
    expect(screen.queryByText("21.5 °C")).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark nearest weather reading on map" })).toBeNull();
  });

  it.each([
    ["absent", {
      state: "absent",
      requestedDay: "2026-07-31",
      servedDay: "2026-07-31",
      evidence: {
        reason: "recorded_absence",
        upstreamResponse: "no rows",
        recordedAt: "2026-08-01T00:00:00Z",
        runId: "weather-test-run",
      },
    }],
    ["not generated", { state: "not_generated", requestedDay: "2026-07-31", reason: "day_not_written" }],
  ] as const)("withholds a completed wrong-day %s envelope", (_label, data) => {
    queries.getWeatherForBbox.mockReturnValue({
      data,
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
    });

    renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(screen.getByText(`Loading ${DAY}; no earlier frame is shown.`)).toBeTruthy();
    expect(screen.queryByText(/2026-07-31/)).toBeNull();
  });

  it("describes aggregate support without treating contributor metadata as a sample count", () => {
    const aggregateObservation: ParquetBrowserWeatherObservation = {
      ...observation,
      support: {
        ...support,
        zoomTier: 9,
        supportKind: "aggregate_cell",
        aggregationMethod: "mean",
        contributorCount: 99,
      },
    };
    queries.getWeatherForBbox.mockReturnValue({
      data: { state: "ready", requestedDay: DAY, servedDay: DAY, truncated: false, data: [aggregateObservation] },
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
    });

    renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={7} />);

    expect(screen.getByText("Nearest to view center")).toBeTruthy();
    expect(screen.getByText("Declared aggregate-cell mean")).toBeTruthy();
    expect(screen.getByText(/0 point readings and 1 declared aggregate cell in view/)).toBeTruthy();
    expect(screen.queryByText(/99/)).toBeNull();
  });

  it("labels a retained frame with its own day while the selected day is pending", () => {
    queries.getWeatherForBbox.mockReturnValue({
      data: { state: "ready", requestedDay: "2026-07-31", servedDay: "2026-07-31", truncated: false, data: [observation] },
      isFetching: true,
      isPlaceholderData: true,
      isError: false,
    });

    renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(screen.getByText(`Showing the retained 2026-07-31 frame while ${DAY} loads.`)).toBeTruthy();
  });

  it("withholds a persisted prior-day frame from the dateless current-day query", () => {
    useTimeSliderStore.setState((state) => ({
      capabilities: state.capabilities === null
        ? null
        : { ...state.capabilities, serverCurrentDate: DAY },
    }));
    queries.getWeatherForBbox.mockReturnValue({
      data: {
        state: "ready",
        requestedDay: "2026-07-31",
        servedDay: "2026-07-31",
        truncated: false,
        data: [observation],
      },
      isFetching: true,
      isPlaceholderData: false,
      isError: false,
    });

    renderWithProviders(<WeatherHistoryReport bbox="-117,43,-115,45" zoom={13} />);

    expect(screen.getByText(`Loading ${DAY}; no earlier frame is shown.`)).toBeTruthy();
    expect(screen.queryByText("21.5 °C")).toBeNull();
  });
});

describe("windDirectionLabel", () => {
  it.each([[0, "N (0°)"], [90, "E (90°)"], [225, "SW (225°)"], [null, null]] as const)(
    "formats %s as %s",
    (degrees, label) => expect(windDirectionLabel(degrees)).toBe(label)
  );
});
