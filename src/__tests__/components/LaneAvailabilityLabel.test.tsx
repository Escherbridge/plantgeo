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
  it("separates available data, configured source delay, and scheduled ingestion", () => {
    render(<LaneAvailabilityLabel label="Temperature" capability={{
      ...capability,
      freshness: {
        publicationLagDays: 5,
        sourceCadenceDays: 1,
        refreshIntervalSeconds: 3600,
        expectedHorizonDay: "2026-09-15",
      },
    }} />);
    expect(screen.getByText("Temperature availability and refresh details:").parentElement?.textContent)
      .toContain("Latest available day 2026-09-15Daily source · 5-day configured delay · Scheduled refresh hourly.");
    expect(screen.getByText(/Configured publication delay/).textContent).toContain(
      "Configured publication delay: 5 days. Scheduled refresh hourly. Target date from configured delay: 2026-09-15."
    );
    expect(screen.getByText(/Configured publication delay/).textContent).toContain(
      "not a guarantee of publication"
    );
  });

  it("does not derive a normal delay from an old ceiling or claim freshness", () => {
    render(<LaneAvailabilityLabel label="Temperature" capability={{
      ...capability, sourceCeilingDay: "2026-01-01",
    }} />);
    expect(screen.getByText("Temperature availability and refresh details:").parentElement?.textContent)
      .toContain("Source cadence unknown");
    expect(screen.getByText(/Publication delay not reported/).textContent)
      .toContain("Refresh schedule not reported.");
    expect(screen.queryByText(/behind|on time|healthy|Configured publication delay/)).toBeNull();
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
    expect(screen.getByText("Soil moisture availability and refresh details:").parentElement?.textContent)
      .toContain("Latest available date unknown");
    expect(screen.getByText(/Configured publication delay/).textContent).toContain("Scheduled refresh daily.");
  });
});
