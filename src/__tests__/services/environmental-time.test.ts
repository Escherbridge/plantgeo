import { afterEach, describe, expect, it, vi } from "vitest";
import {
  firmsDayRange,
  isFreshObservation,
  parseFirmsObservationTime,
  parseZonedObservationTime,
} from "@/lib/server/services/environmental-time";

describe("environmental observation time", () => {
  it("normalizes FIRMS acquisition fields as UTC", () => {
    expect(
      parseFirmsObservationTime({ acqDate: "2026-07-19", acqTime: "42" })
    ).toBe("2026-07-19T00:42:00.000Z");
  });

  it("rejects invalid dates and unzoned timestamps", () => {
    expect(
      parseFirmsObservationTime({ acqDate: "2026-02-31", acqTime: "1200" })
    ).toBeNull();
    expect(parseZonedObservationTime("2026-07-19T12:00:00")).toBeNull();
  });

  it("rejects stale and implausibly future observations", () => {
    const now = Date.parse("2026-07-19T12:00:00Z");
    expect(
      isFreshObservation("2026-07-19T11:00:00Z", 2 * 60 * 60 * 1_000, now)
    ).toBe(true);
    expect(
      isFreshObservation("2026-07-19T09:00:00Z", 2 * 60 * 60 * 1_000, now)
    ).toBe(false);
    expect(
      isFreshObservation("2026-07-19T12:06:00Z", 2 * 60 * 60 * 1_000, now)
    ).toBe(false);
  });
});

describe("firmsDayRange", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to 2 when unset", () => {
    vi.stubEnv("FIRMS_DAY_RANGE", "");
    expect(firmsDayRange()).toBe(2);
  });

  it("clamps in-range integers to themselves", () => {
    vi.stubEnv("FIRMS_DAY_RANGE", "5");
    expect(firmsDayRange()).toBe(5);
  });

  it("clamps out-of-range integers to 1-10", () => {
    vi.stubEnv("FIRMS_DAY_RANGE", "0");
    expect(firmsDayRange()).toBe(1);
    vi.stubEnv("FIRMS_DAY_RANGE", "99");
    expect(firmsDayRange()).toBe(10);
  });

  it("rejects non-numeric strings instead of truncating them", () => {
    vi.stubEnv("FIRMS_DAY_RANGE", "5abc");
    expect(firmsDayRange()).toBe(2);
    vi.stubEnv("FIRMS_DAY_RANGE", "-3");
    expect(firmsDayRange()).toBe(2);
    vi.stubEnv("FIRMS_DAY_RANGE", "3.5");
    expect(firmsDayRange()).toBe(2);
  });
});
