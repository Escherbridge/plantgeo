import { afterEach, describe, expect, it, vi } from "vitest";
import { getCurrentWeather } from "@/lib/server/services/weather";

function weatherResponse(time: number): Response {
  return new Response(
    JSON.stringify({
      current: {
        time,
        temperature_2m: 24.5,
        relative_humidity_2m: 37,
        wind_speed_10m: 4.2,
        wind_direction_10m: 225,
        precipitation: 0,
      },
    }),
    { headers: { "Content-Type": "application/json" } }
  );
}

describe("current weather acquisition contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requests UTC epoch time and returns a fresh normalized observation", async () => {
    const observedSeconds = Math.floor(Date.now() / 1_000);
    const fetchMock = vi.fn().mockResolvedValue(weatherResponse(observedSeconds));
    vi.stubGlobal("fetch", fetchMock);

    const observation = await getCurrentWeather(39.7392, -104.9903);

    const requestedUrl = fetchMock.mock.calls[0]?.[0] as URL;
    expect(requestedUrl.searchParams.get("timeformat")).toBe("unixtime");
    expect(requestedUrl.searchParams.get("timezone")).toBe("GMT");
    expect(observation.observedAt).toBe(
      new Date(observedSeconds * 1_000).toISOString()
    );
  });

  it("rejects a stale payload instead of persisting it as current", async () => {
    const staleSeconds = Math.floor((Date.now() - 4 * 60 * 60 * 1_000) / 1_000);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(weatherResponse(staleSeconds)));

    await expect(getCurrentWeather(39.7392, -104.9903)).rejects.toThrow(
      "stale current observation"
    );
  });
});
