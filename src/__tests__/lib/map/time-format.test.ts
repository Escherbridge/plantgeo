import { describe, expect, it } from "vitest";
import {
  formatAbsoluteDate,
  formatAbsoluteDateTime,
  formatRelativeTime,
  formatTimestampWithRelative,
  toIsoTimestamp,
} from "@/lib/map/time-format";

describe("toIsoTimestamp", () => {
  it("passes ISO strings through as a normalized ISO timestamp", () => {
    expect(toIsoTimestamp("2026-07-14T09:30:00Z")).toBe("2026-07-14T09:30:00.000Z");
  });

  it("converts epoch milliseconds, as a number and as a stringified number", () => {
    expect(toIsoTimestamp(1_753_000_000_000)).toBe("2025-07-20T08:26:40.000Z");
    expect(toIsoTimestamp("1753000000000")).toBe("2025-07-20T08:26:40.000Z");
  });

  it("treats sub-1e11 epoch values as seconds", () => {
    expect(toIsoTimestamp(1_753_000_000)).toBe("2025-07-20T08:26:40.000Z");
  });

  it("returns null for absent or unparseable values", () => {
    expect(toIsoTimestamp(null)).toBeNull();
    expect(toIsoTimestamp(undefined)).toBeNull();
    expect(toIsoTimestamp("")).toBeNull();
    expect(toIsoTimestamp("   ")).toBeNull();
    expect(toIsoTimestamp("not a date")).toBeNull();
    expect(toIsoTimestamp(NaN)).toBeNull();
    expect(toIsoTimestamp({})).toBeNull();
  });
});

describe("absolute formatting", () => {
  it("renders a calendar date and a date-time", () => {
    const iso = "2026-07-14T09:30:00Z";
    expect(formatAbsoluteDate(iso)).toMatch(/2026/);
    expect(formatAbsoluteDate(iso)).not.toMatch(/:/);
    expect(formatAbsoluteDateTime(iso)).toMatch(/2026/);
    expect(formatAbsoluteDateTime(iso)).toMatch(/\d:\d{2}/);
  });

  it("returns null instead of an Invalid Date string", () => {
    expect(formatAbsoluteDate(null)).toBeNull();
    expect(formatAbsoluteDate("nope")).toBeNull();
    expect(formatAbsoluteDateTime(undefined)).toBeNull();
  });
});

describe("formatRelativeTime", () => {
  it("buckets by minute, hour, and day", () => {
    expect(formatRelativeTime(new Date(Date.now() - 10_000).toISOString())).toBe("just now");
    expect(formatRelativeTime(new Date(Date.now() - 45 * 60_000).toISOString())).toBe("45m ago");
    expect(formatRelativeTime(new Date(Date.now() - 3 * 3600_000).toISOString())).toBe("3h ago");
    expect(formatRelativeTime(new Date(Date.now() - 2 * 86400_000).toISOString())).toBe("2d ago");
  });

  it("clamps future timestamps to 'just now' rather than a negative age", () => {
    expect(formatRelativeTime(new Date(Date.now() + 60_000).toISOString())).toBe("just now");
  });

  it("returns null when unparseable", () => {
    expect(formatRelativeTime("nope")).toBeNull();
  });
});

describe("formatTimestampWithRelative", () => {
  it("pairs the absolute time with its relative age", () => {
    const formatted = formatTimestampWithRelative(
      new Date(Date.now() - 3 * 3600_000).toISOString(),
    );
    expect(formatted).toMatch(/\(3h ago\)$/);
    expect(formatted).toMatch(/\d:\d{2}/);
  });

  it("returns null when unparseable", () => {
    expect(formatTimestampWithRelative(null)).toBeNull();
  });
});
