import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { LaneAvailabilityLabel } from "@/components/map/layer-panel/LaneAvailabilityLabel";
import type { SliderLayerCapability } from "@/types/time-slider";

const capability: SliderLayerCapability = {
  layerName: "climate-field-temperature",
  temporalKind: "daily_series",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2022-01-01",
  latestObservedDate: "2026-09-15",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

afterEach(cleanup);

describe("LaneAvailabilityLabel", () => {
  it("shows the readable edge, source cadence, and refresh check once", () => {
    render(<LaneAvailabilityLabel label="Temperature" capability={{
      ...capability,
      freshness: {
        publicationLagDays: 5,
        sourceCadenceDays: 1,
        refreshIntervalSeconds: 3600,
        expectedHorizonDay: "2026-09-15",
      },
    }} />);
    const text = screen.getByText("Temperature timing:").parentElement?.textContent ?? "";
    expect(text).toContain(
      "Available through 2026-09-15 · Source daily · Checked hourly"
    );
    expect(text).not.toMatch(/delay|target date|guarantee/i);
  });

  it("omits timing fragments the capability did not report", () => {
    render(<LaneAvailabilityLabel label="Temperature" capability={{
      ...capability, sourceCeilingDay: "2026-01-01",
    }} />);
    const text = screen.getByText("Temperature timing:").parentElement?.textContent ?? "";
    expect(text).toBe("Temperature timing: Available through 2026-09-15");
    expect(text).not.toMatch(/unknown|refresh|checked|delay|behind|on time|healthy/i);
  });

  it("does not replace an unknown available date with the expected source date", () => {
    render(<LaneAvailabilityLabel label="Soil moisture" capability={{
      ...capability,
      latestObservedDate: null,
      freshness: {
        publicationLagDays: 9, sourceCadenceDays: 1,
        refreshIntervalSeconds: 86400, expectedHorizonDay: "2026-09-11",
      },
    }} />);
    const text = screen.getByText("Soil moisture timing:").parentElement?.textContent ?? "";
    expect(text).toContain("Latest date unknown · Source daily · Checked daily");
    expect(text).not.toMatch(/delay|2026-09-11/);
  });
});
