import { afterEach, describe, expect, it, vi } from "vitest";
import { readWeatherForecast, weatherForecastTool } from "./server";
import { fixtureQuery, makeForecastFixture } from "./test-fixture";

afterEach(() => { vi.unstubAllEnvs(); vi.clearAllMocks(); });

describe("local forecast transport and tool parity", () => {
  it("disables production and non-loopback services without fetching", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("WEATHER_FORECAST_LOCAL_URL", "http://127.0.0.1:8099");
    await expect(readWeatherForecast(fixtureQuery)).rejects.toThrow(/not enabled/);
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("WEATHER_FORECAST_LOCAL_URL", "https://provider.example");
    await expect(readWeatherForecast(fixtureQuery)).rejects.toThrow(/loopback/);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("uses the identical reader for agent and HTTP results", async () => {
    vi.stubEnv("WEATHER_FORECAST_LOCAL_URL", "http://127.0.0.1:8099");
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(makeForecastFixture())));
    expect(weatherForecastTool.execute).toBe(readWeatherForecast);
    expect((await weatherForecastTool.execute(fixtureQuery)).request).toEqual(fixtureQuery);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects oversize responses and does not fetch a fallback", async () => {
    vi.stubEnv("WEATHER_FORECAST_LOCAL_URL", "http://127.0.0.1:8099");
    vi.mocked(fetch).mockResolvedValueOnce(new Response("x".repeat(256 * 1024 + 1)));
    await expect(readWeatherForecast(fixtureQuery)).rejects.toThrow(/byte limit/);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
