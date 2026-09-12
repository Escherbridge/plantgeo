import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup, renderHook } from "@testing-library/react";
import type { CircleLayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import { WeatherDetails } from "@/components/panels/WeatherDetails";
import { scalarForecastFeatures, windForecastFeatures } from "@/components/map/layers/weather-forecast/presentation";
import { useForecastMapSource } from "@/components/map/layers/weather-forecast/useForecastMapSource";
import { forecastDays, formatForecastValue, windVectorPresentation,
  type SelectedWeatherForecast, type WeatherForecastValue } from "@/lib/environmental/weather-forecast";

function value(overrides: Partial<WeatherForecastValue> = {}): WeatherForecastValue {
  return { run_id: "ifs-run", sample_id: "boise", latitude: 43.62, longitude: -116.16,
    variable: "temperature_2m", unit: "degC", valid_at: "2026-09-08T01:00:00Z", lead_seconds: 3600,
    interval_start: null, interval_end: null, value: 21, status: "available", ...overrides };
}

function forecast(): SelectedWeatherForecast {
  return {
    selection: { run_id: "ifs-run", latitude: 43.615, longitude: -116.2023,
      start: "2026-09-08T00:00:00Z", end: "2026-09-18T00:00:00Z", timezone: "America/Boise" },
    status: "available", sample_distance_m: 3450,
    run: { run_id: "ifs-run", product_id: "ifs-sampled", provider: "Open-Meteo", model: "ecmwf_ifs",
      model_version: null, model_init_at: "2026-09-08T00:00:00Z", provider_issued_at: null,
      fetched_at: "2026-09-12T01:00:00Z", admitted_at: "2026-09-12T01:10:00Z", published_at: "2026-09-12T01:20:00Z",
      licence: "CC BY 4.0", source_url: "https://single-runs-api.open-meteo.com/v1/forecast",
      source_payload_sha256: "a".repeat(64), support: { kind: "sampled_point", represented_support: "sample_coordinate",
        source_resolution_m: null }, variables: ["temperature_2m"] },
    values: [value()],
  };
}

describe("forecast presentation", () => {
  it("preserves zero precipitation and explicit missingness", () => {
    expect(formatForecastValue(value({ variable: "precipitation", value: 0, unit: "mm" }))).toBe("0 mm");
    expect(formatForecastValue(value({ status: "missing", value: null }))).toContain("No forecast value");
    expect(formatForecastValue(value({ unit: "F" }))).toBe("Invalid forecast value");
  });

  it("cannot paint another run, hour, missing value or inferred cell", () => {
    const result = scalarForecastFeatures([value(), value({ sample_id: "old", run_id: "old" }),
      value({ sample_id: "absent", value: null, status: "missing" })], "ifs-run", value().valid_at, "temperature_2m");
    expect(result.features).toHaveLength(1);
    expect(result.features[0].geometry.type).toBe("Point");
    expect(scalarForecastFeatures([value()], "ifs-run", "2025-04-28T00:00:00Z", "temperature_2m").features).toEqual([]);
    expect(scalarForecastFeatures([value(), value()], "ifs-run", value().valid_at, "temperature_2m").features).toEqual([]);
  });

  it("uses paired components at identical sample coordinates and no vector for calm", () => {
    const u = value({ variable: "wind_u_10m", unit: "m/s", value: 0 });
    const v = value({ variable: "wind_v_10m", unit: "m/s", value: -10 });
    const points = windForecastFeatures([u, v], "ifs-run", u.valid_at).features;
    expect(points[0].properties).toMatchObject({ from: 0, to: 180, speed: 10 });
    expect(windForecastFeatures([u, { ...v, longitude: 12 }], "ifs-run", u.valid_at).features).toEqual([]);
    expect(windVectorPresentation(0, 0)).toEqual({ speed: 0, from: null, to: null });
  });

  it.each([
    ["2026-03-08T07:00:00Z", 23], ["2026-11-01T06:00:00Z", 25],
  ])("summarizes a complete DST day from UTC hours %s", (start, count) => {
    const rows: WeatherForecastValue[] = [];
    for (let i = 0; i < count; i++) {
      const at = new Date(Date.parse(start) + i * 3600000).toISOString();
      const end = new Date(Date.parse(at) + 3600000).toISOString();
      rows.push(value({ valid_at: at, value: i }));
      rows.push(value({ variable: "precipitation", unit: "mm", value: 0,
        valid_at: end, interval_start: at, interval_end: end }));
    }
    const day = forecastDays(rows, "America/Boise")[0];
    expect(day).toMatchObject({ complete: true, low: 0, high: count - 1, precipitation: 0 });
    expect(forecastDays(rows.slice(2), "America/Boise")[0].complete).toBe(false);
  });

  it("clears the card for a delayed other-place or historical response", () => {
    const data = forecast();
    render(<WeatherDetails productId="ifs-sampled" forecast={data} selected={{ ...data.selection, start: "2025-04-28T00:00:00Z" }}
      validAt={value().valid_at} onSelectTime={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toContain("unavailable");
    expect(screen.queryByRole("table")).toBeNull();
    cleanup();
    render(<WeatherDetails productId="ifs-sampled" forecast={data} selected={{ ...data.selection, longitude: 0 }}
      validAt={value().valid_at} onSelectTime={vi.fn()} />);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("displays selected coordinates and allows keyboard-native hourly selection", () => {
    const data = forecast();
    const select = vi.fn();
    render(<WeatherDetails productId="ifs-sampled" forecast={data} selected={data.selection} validAt={value().valid_at} onSelectTime={select} />);
    expect(screen.getByText(/43.6150, -116.2023/)).toBeDefined();
    expect(screen.getByText(/Sample 3.45 km/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { pressed: true }));
    expect(select).toHaveBeenCalledWith(new Date(value().valid_at).toISOString());
    expect(screen.getByText("Not supplied by provider")).toBeDefined();
  });

  it("matches Python Z and JavaScript .000Z timestamps as the same UTC instant", () => {
    const data = forecast();
    const selected = { ...data.selection, start: new Date(data.selection.start).toISOString(),
      end: new Date(data.selection.end).toISOString() };
    render(<WeatherDetails productId="ifs-sampled" forecast={data} selected={selected}
      validAt={new Date(value().valid_at).toISOString()} onSelectTime={vi.fn()} />);
    expect(screen.getByRole("button", { pressed: true })).toBeDefined();
    expect(scalarForecastFeatures(data.values, "ifs-run", new Date(value().valid_at).toISOString(), "temperature_2m").features).toHaveLength(1);
  });

  it.each([
    ["not_yet_generated", "This forecast interval has not been generated"],
    ["upstream_unavailable", "The forecast provider is unavailable"],
  ] as const)("preserves a %s response with no published run", (status, label) => {
    const data = forecast();
    render(<WeatherDetails productId="ifs-sampled" forecast={{ ...data, status, run: null, values: [] }}
      selected={data.selection} validAt={value().valid_at} onSelectTime={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toBe(label);
  });

  it("rebuilds a style from current samples and safely unmounts after the map was removed", () => {
    const listeners = new Map<string, () => void>();
    let removed = false;
    let source: { setData: ReturnType<typeof vi.fn> } | undefined;
    let addedLayer = false;
    const mock = {
      isStyleLoaded: () => !removed,
      getStyle: () => removed ? undefined : {},
      getSource: () => { if (removed) throw new Error("removed map"); return source; },
      addSource: vi.fn(() => { source = { setData: vi.fn() }; }),
      getLayer: () => { if (removed) throw new Error("removed map"); return addedLayer; },
      addLayer: vi.fn(() => { addedLayer = true; }),
      removeSource: vi.fn(() => { source = undefined; }),
      removeLayer: vi.fn(() => { addedLayer = false; }),
      on: vi.fn((event: string, listener: () => void) => listeners.set(event, listener)),
      off: vi.fn((event: string) => listeners.delete(event)),
    };
    const layer: CircleLayerSpecification = { id: "forecast", source: "forecast", type: "circle" };
    const first = scalarForecastFeatures([value()], "ifs-run", value().valid_at, "temperature_2m");
    const second = scalarForecastFeatures([value({ value: 0 })], "ifs-run", value().valid_at, "temperature_2m");
    const { rerender, unmount } = renderHook(({ data }) =>
      useForecastMapSource(mock as unknown as MapLibreMap, "forecast", layer, data), { initialProps: { data: first } });
    rerender({ data: second });
    source = undefined;
    addedLayer = false;
    listeners.get("style.load")!();
    expect(mock.addSource).toHaveBeenLastCalledWith("forecast", { type: "geojson", data: second });
    removed = true;
    expect(() => unmount()).not.toThrow();
    expect(listeners.size).toBe(0);
  });
});
