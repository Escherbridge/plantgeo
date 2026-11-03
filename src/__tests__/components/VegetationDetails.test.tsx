import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const display = vi.hoisted(() => ({ source: "measured", unavailableReason: null as string | null }));

vi.mock("@/lib/map/layer-toggle-context", () => ({
  useLayerDay: () => ({ selectedDate: "2026-08-31" }),
  useLayerRenderState: () => ({ unavailableReason: display.unavailableReason }),
  useVegetationDisplayMode: () => ({ compositePeriod: null, compositeUnavailableReason: null }),
}));
vi.mock("@/stores/vegetation-store", () => ({
  useVegetationStore: () => ({ source: display.source, setSource: vi.fn() }),
}));
import { VegetationDetails } from "@/components/panels/VegetationDetails";

beforeEach(() => { display.source = "measured"; display.unavailableReason = null; });

describe("VegetationDetails published controls", () => {
  it("does not promise gap-free satellite imagery or attach measured coverage warnings to it", () => {
    display.source = "satellite";
    display.unavailableReason = "Measured data has not been published";
    render(<VegetationDetails />);
    expect(screen.getByText(/possible missing imagery/)).toBeTruthy();
    expect(screen.queryByText(/gap-free/)).toBeNull();
    expect(screen.queryByText(display.unavailableReason)).toBeNull();
  });
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
