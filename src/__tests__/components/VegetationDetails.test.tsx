import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/map/layer-toggle-context", () => ({
  useLayerDay: () => ({ selectedDate: "2026-08-31" }),
  useLayerRenderState: () => ({ unavailableReason: null }),
  useVegetationDisplayMode: () => ({ compositePeriod: null, compositeUnavailableReason: null }),
}));
vi.mock("@/stores/vegetation-store", () => ({
  useVegetationStore: () => ({ source: "measured", setSource: vi.fn() }),
}));
import { VegetationDetails } from "@/components/panels/VegetationDetails";

describe("VegetationDetails published controls", () => {
  it("keeps measured and satellite controls without deferred tabs or duplicate opacity", () => {
    render(<VegetationDetails />);
    expect(screen.getByRole("button", { name: "Measured grid" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Satellite (MODIS)" })).toBeTruthy();
    expect(screen.getByText("NDVI Legend")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByText("Forecast")).toBeNull();
    expect(screen.queryByText("Land Cover")).toBeNull();
    expect(screen.queryByText("Layer opacity")).toBeNull();
  });
});
