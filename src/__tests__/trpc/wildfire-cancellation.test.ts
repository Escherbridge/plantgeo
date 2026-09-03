import { beforeEach, describe, expect, it, vi } from "vitest";

// The router imports `@/lib/server/db` transitively; a live connection is not what is under test.
vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({
  auth: vi.fn(() => Promise.resolve(null)),
  getServerSession: vi.fn(() => Promise.resolve(null)),
}));

// Only the two readers are replaced. `rejectAborted` stays the real one: it is the subject.
vi.mock("@/lib/server/services/parquet-trpc-readers", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/server/services/parquet-trpc-readers")>();
  return {
    ...actual,
    getParquetFireDetections: vi.fn(),
    getParquetWeatherObservations: vi.fn(),
  };
});

import { TRPCError } from "@trpc/server";
import {
  getParquetFireDetections,
  getParquetWeatherObservations,
} from "@/lib/server/services/parquet-trpc-readers";
import { wildfireRouter } from "@/lib/server/trpc/routers/wildfire";

const mockedFire = vi.mocked(getParquetFireDetections);
const mockedWeather = vi.mocked(getParquetWeatherObservations);

const caller = wildfireRouter.createCaller({
  db: {} as never,
  session: null,
});

const abortedResult = {
  state: "upstream_unavailable" as const,
  fault: { kind: "aborted" as const, message: "Upstream request was cancelled by its caller" },
};

const fireInput = { bbox: "-125,42,-111,49", date: "2026-08-20", zoom: 9, dayRange: 1 };
const weatherInput = { bbox: "-125,42,-111,49", date: "2026-08-20", zoom: 9 };

beforeEach(() => {
  vi.clearAllMocks();
});

/**
 * An abandoned read must never come back as a 200 payload. react-query STORES what a query
 * resolves with, so an `{ kind: "aborted" }` result returned as data would be cached against the
 * viewport key and replayed to the next reader of that key as though the warehouse had answered.
 * `rejectAborted` puts it on the error path instead, where the retry and placeholder rules
 * already handle it.
 */
describe("an aborted wildfire read is thrown, never resolved", () => {
  it("rejects getFireDetections rather than returning the aborted payload", async () => {
    mockedFire.mockResolvedValue(abortedResult);

    await expect(caller.getFireDetections(fireInput)).rejects.toThrow(TRPCError);
    await expect(caller.getFireDetections(fireInput)).rejects.toMatchObject({
      code: "CLIENT_CLOSED_REQUEST",
    });
  });

  it("rejects getWeatherForBbox on the same fault", async () => {
    mockedWeather.mockResolvedValue(abortedResult);

    await expect(caller.getWeatherForBbox(weatherInput)).rejects.toMatchObject({
      code: "CLIENT_CLOSED_REQUEST",
    });
  });

  // The other six fault kinds are claims about the UPSTREAM, and the map captions them rather
  // than blanking. Turning those into throws too would delete that whole surface, so the guard
  // has to be narrow -- this is the case that pins it.
  it("still returns an upstream outage as data, so the map can caption it", async () => {
    const outage = {
      state: "upstream_unavailable" as const,
      fault: { kind: "http" as const, message: "503 from the data service", status: 503 },
    };
    mockedFire.mockResolvedValue(outage);

    await expect(caller.getFireDetections(fireInput)).resolves.toEqual(outage);
  });

  it("passes a ready answer straight through", async () => {
    const ready = {
      state: "ready" as const,
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      data: { firstDay: "2026-08-20", lastDay: "2026-08-20", cells: [], days: [] },
      truncated: false,
    };
    mockedFire.mockResolvedValue(ready);

    await expect(caller.getFireDetections(fireInput)).resolves.toEqual(ready);
  });

  it("threads the resolver's signal into the reader it wraps", async () => {
    mockedFire.mockResolvedValue({
      state: "not_generated" as const,
      requestedDay: "2026-08-20",
      reason: "day_not_written" as const,
    });

    await caller.getFireDetections(fireInput);

    expect(mockedFire).toHaveBeenCalledWith(
      expect.objectContaining({
        bbox: fireInput.bbox,
        date: fireInput.date,
        mapZoom: fireInput.zoom,
        dayRange: 1,
      })
    );
    expect(Object.keys(mockedFire.mock.calls[0][0])).toContain("signal");
  });
});
