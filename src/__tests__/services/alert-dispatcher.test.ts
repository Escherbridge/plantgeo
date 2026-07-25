import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/services/alert-engine", () => ({
  checkFireProximityAlerts: vi.fn(),
  checkDroughtAlerts: vi.fn(),
  checkStreamflowAlerts: vi.fn(),
  checkPriorityZoneAlerts: vi.fn(),
}));
vi.mock("@/lib/server/services/email", () => ({ sendAlertEmail: vi.fn() }));
vi.mock("@/lib/server/services/realtime", () => ({ publish: vi.fn() }));

import { buildAlertDedupeKey } from "@/lib/server/jobs/alert-dispatcher";

describe("alert dispatcher dedupe identity", () => {
  it("is stable within a UTC day and isolated by location", () => {
    const morning = new Date("2026-07-20T01:00:00.000Z");
    const evening = new Date("2026-07-20T23:59:00.000Z");
    const first = buildAlertDedupeKey("user", "fire_proximity", "location-a", morning);

    expect(
      buildAlertDedupeKey("user", "fire_proximity", "location-a", evening)
    ).toBe(first);
    expect(
      buildAlertDedupeKey("user", "fire_proximity", "location-b", evening)
    ).not.toBe(first);
  });
});
