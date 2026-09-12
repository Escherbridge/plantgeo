import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return {
    ...actual,
    providerUrl: vi.fn(),
    fetchBoundedJson: vi.fn(),
  };
});

import {
  fetchBoundedJson,
  providerUrl,
  UpstreamConfigurationError,
} from "@/lib/server/http/bounded-upstream";
import {
  getSelectedWeatherForecast,
  getWeatherForecastCapability,
  getWeatherForecastField,
  selectedWeatherForecastRequestSchema,
  WeatherForecastContractError,
} from "@/lib/server/services/weather-forecast";

const mockedProviderUrl = vi.mocked(providerUrl);
const mockedFetch = vi.mocked(fetchBoundedJson);
const PRODUCT_ID = "ecmwf-ifs-single-run";
const RUN_ID = "ecmwf-ifs-20260908T0000";
const START = "2026-09-08T00:00:00Z";
const END = "2026-09-08T02:00:00Z";

function run() {
  return {
    schema_version: "weather-forecast/v1",
    product_kind: "forecast",
    forecast_kind: "deterministic",
    run_id: RUN_ID,
    product_id: PRODUCT_ID,
    provider: "open-meteo",
    model: "ecmwf-ifs",
    model_version: null,
    model_init_at: START,
    provider_issued_at: null,
    fetched_at: "2026-09-08T00:20:00Z",
    admitted_at: "2026-09-08T00:30:00Z",
    published_at: "2026-09-08T00:40:00Z",
    licence: "CC BY 4.0 provider attribution required",
    source_url: "https://example.test/single-runs",
    source_payload_sha256: "a".repeat(64),
    support: {
      kind: "sampled_point",
      represented_support: "sample_coordinate",
      source_resolution_m: 9_000,
      resolution_evidence: "provider grid",
    },
    variables: ["temperature_2m"],
  };
}

function capability(status: "available" | "not_yet_generated" = "available") {
  const hasRun = status === "available";
  return {
    schema_version: "weather-forecast-capability/v1",
    product_id: PRODUCT_ID,
    source: "local",
    status,
    active_run_id: hasRun ? RUN_ID : null,
    run: hasRun ? run() : null,
    support: hasRun ? "sampled_points" : null,
    variables: hasRun ? ["temperature_2m"] : null,
    valid_start: hasRun ? START : null,
    valid_end: hasRun ? END : null,
    sample_count: hasRun ? 1 : null,
    row_count: hasRun ? 2 : null,
  };
}

const selectedRequest = {
  productId: PRODUCT_ID,
  runId: RUN_ID,
  longitude: -116.2023,
  latitude: 43.615,
  start: START,
  end: END,
  timezone: "America/Boise",
  zoom: 13,
  maxDistanceM: 10_000,
};

function selected() {
  return {
    selection: {
      run_id: RUN_ID,
      longitude: selectedRequest.longitude,
      latitude: selectedRequest.latitude,
      start: START,
      end: END,
      timezone: selectedRequest.timezone,
    },
    status: "available",
    run: run(),
    zoom: 13,
    sample_distance_m: 1_517.25,
    values: [
      {
        run_id: RUN_ID,
        sample_id: "sample-1",
        longitude: -116.1875,
        latitude: 43.625,
        variable: "temperature_2m",
        unit: "degC",
        valid_at: START,
        lead_seconds: 0,
        interval_start: null,
        interval_end: null,
        value: 18.5,
        status: "available",
      },
    ],
  };
}

const fieldRequest = {
  productId: PRODUCT_ID,
  runId: RUN_ID,
  bbox: [-117, 43, -115, 44] as [number, number, number, number],
  start: START,
  end: END,
  variable: "temperature_2m" as const,
  zoom: 11,
};

function field() {
  return {
    product_id: PRODUCT_ID,
    run_id: RUN_ID,
    bbox: fieldRequest.bbox,
    start: START,
    end: END,
    variable: fieldRequest.variable,
    zoom: 9,
    status: "outside_domain",
    run: run(),
    values: [],
  };
}

describe("weather forecast transport contract", () => {
  beforeEach(() => {
    mockedProviderUrl.mockReset();
    mockedFetch.mockReset();
    mockedProviderUrl.mockReturnValue(new URL("http://agri.internal:8000"));
  });

  it("reads the capability with a bounded request and preserves cancellation", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue(capability());

    const result = await getWeatherForecastCapability({ productId: PRODUCT_ID }, controller.signal);

    const [url, init, bounds] = mockedFetch.mock.calls[0];
    expect((url as URL).pathname).toBe("/api/v1/weather-forecast/capability");
    expect((url as URL).searchParams.get("product_id")).toBe(PRODUCT_ID);
    expect(init).toMatchObject({ method: "GET", headers: { Accept: "application/json" } });
    expect(bounds).toMatchObject({ maxBytes: 4 * 1024 * 1024, timeoutMs: 15_000, signal: controller.signal });
    expect(result.active_run_id).toBe(RUN_ID);
  });

  it("returns an explicit unavailable capability when the service URL is not configured", async () => {
    mockedProviderUrl.mockImplementation(() => {
      throw new UpstreamConfigurationError("AGRI_DATA_SERVICE_URL is not configured");
    });

    await expect(getWeatherForecastCapability({ productId: PRODUCT_ID })).resolves.toMatchObject({
      product_id: PRODUCT_ID,
      status: "upstream_unavailable",
      active_run_id: null,
      run: null,
    });
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("sends every selected identity axis and accepts explicit content absence", async () => {
    mockedFetch.mockResolvedValue({
      ...selected(),
      status: "not_yet_generated",
      run: null,
      sample_distance_m: null,
      values: [],
    });

    const result = await getSelectedWeatherForecast(selectedRequest);

    const requestedUrl = mockedFetch.mock.calls[0][0] as URL;
    expect(Object.fromEntries(requestedUrl.searchParams)).toEqual({
      product_id: PRODUCT_ID,
      run_id: RUN_ID,
      longitude: String(selectedRequest.longitude),
      latitude: String(selectedRequest.latitude),
      start: START,
      end: END,
      timezone: "America/Boise",
      zoom: "13",
      max_distance_m: "10000",
    });
    expect(result.status).toBe("not_yet_generated");
  });

  it("rejects a selected payload that substitutes the pinned run", async () => {
    mockedFetch.mockResolvedValue({
      ...selected(),
      selection: { ...selected().selection, run_id: "ecmwf-ifs-other-run" },
    });

    await expect(getSelectedWeatherForecast(selectedRequest)).rejects.toBeInstanceOf(
      WeatherForecastContractError
    );
  });

  it("resolves the field zoom tier and rejects substituted field identity", async () => {
    mockedFetch.mockResolvedValue(field());
    await expect(getWeatherForecastField(fieldRequest)).resolves.toMatchObject({ zoom: 9 });

    const requestedUrl = mockedFetch.mock.calls[0][0] as URL;
    expect(requestedUrl.pathname).toBe("/api/v1/weather-forecast/field");
    expect(requestedUrl.searchParams.get("bbox")).toBe("-117,43,-115,44");

    mockedFetch.mockResolvedValue({ ...field(), variable: "cloud_cover" });
    await expect(getWeatherForecastField(fieldRequest)).rejects.toBeInstanceOf(
      WeatherForecastContractError
    );
  });

  it("rejects non-hourly windows, antimeridian bboxes, and unknown request keys", async () => {
    expect(
      selectedWeatherForecastRequestSchema.safeParse({
        ...selectedRequest,
        start: "2026-09-08T00:30:00Z",
      }).success
    ).toBe(false);
    await expect(
      getWeatherForecastField({ ...fieldRequest, bbox: [170, -20, -170, 20] })
    ).rejects.toThrow();
    expect(
      selectedWeatherForecastRequestSchema.safeParse({ ...selectedRequest, fallbackRunId: RUN_ID }).success
    ).toBe(false);
  });

  it("rejects extra response fields instead of silently stripping contract drift", async () => {
    mockedFetch.mockResolvedValue({ ...capability("not_yet_generated"), fallback_run_id: RUN_ID });

    await expect(getWeatherForecastCapability({ productId: PRODUCT_ID })).rejects.toBeInstanceOf(
      WeatherForecastContractError
    );
  });
});
