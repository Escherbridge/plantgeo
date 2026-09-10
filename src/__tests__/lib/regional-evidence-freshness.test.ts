import { describe, expect, it } from "vitest";
import { regionalEvidenceFreshnessState, regionalEvidenceSnapshotDay } from "@/lib/regional-intelligence";

const now = Date.parse("2026-09-10T12:00:00Z");

describe("regional evidence release markers", () => {
  it("recognizes an undated static release only for soil properties", () => {
    expect(regionalEvidenceFreshnessState("soilProperties", "static_release_untimed", now)).toBe("available");
    expect(regionalEvidenceFreshnessState("weatherObservations", "static_release_untimed", now)).toBe("unavailable");
  });

  it("applies the existing perimeter age limit to capture days", () => {
    expect(regionalEvidenceFreshnessState("firePerimeters", "snapshot_captured_2026-09-01", now)).toBe("available");
    expect(regionalEvidenceFreshnessState("firePerimeters", "snapshot_captured_2026-08-01", now)).toBe("stale");
    expect(regionalEvidenceFreshnessState("firePerimeters", "snapshot_captured_2026-09-11", now)).toBe("unavailable");
    expect(regionalEvidenceFreshnessState("streamflow", "snapshot_captured_2026-09-01", now)).toBe("unavailable");
  });

  it("rejects malformed and impossible capture dates instead of normalizing them", () => {
    for (const value of ["snapshot_captured_2026-02-30", "snapshot_captured_2026-9-1", "snapshot_captured_unknown"]) {
      expect(regionalEvidenceSnapshotDay("firePerimeters", value)).toBeNull();
      expect(regionalEvidenceFreshnessState("firePerimeters", value, now)).toBe("unavailable");
    }
  });
});
