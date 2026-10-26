import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InterventionVisualizationModeControl } from "@/components/map/visualization/InterventionVisualizationModeControl";
import { useInterventionVisualizationStore } from "@/components/map/visualization/intervention-visualization-store";

beforeEach(() => {
  // Real store, reset to its documented default rather than mocked: this is the one piece of
  // state `InterventionVisualizationLayer` reads independently elsewhere in the tree, so the
  // flow worth proving is "a click here is the thing that changes what the map would draw",
  // which a mocked store cannot demonstrate.
  useInterventionVisualizationStore.setState({ mode: "off" });
});

afterEach(() => {
  cleanup();
});

describe("InterventionVisualizationModeControl", () => {
  it("renders two mutually exclusive options with 'off' checked by default", () => {
    render(<InterventionVisualizationModeControl />);

    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(screen.getByTestId("intervention-visualization-mode-off").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("intervention-visualization-mode-heatmap").getAttribute("aria-checked")).toBe("false");
  });

  it("selecting a mode writes the shared store and flips which option is checked", () => {
    render(<InterventionVisualizationModeControl />);

    fireEvent.click(screen.getByTestId("intervention-visualization-mode-heatmap"));

    expect(useInterventionVisualizationStore.getState().mode).toBe("heatmap");
    expect(screen.getByTestId("intervention-visualization-mode-heatmap").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("intervention-visualization-mode-off").getAttribute("aria-checked")).toBe("false");
  });

  it("returning to off unchecks the heatmap so the map stops drawing it", () => {
    render(<InterventionVisualizationModeControl />);

    fireEvent.click(screen.getByTestId("intervention-visualization-mode-heatmap"));
    fireEvent.click(screen.getByTestId("intervention-visualization-mode-off"));

    expect(useInterventionVisualizationStore.getState().mode).toBe("off");
    expect(screen.getByTestId("intervention-visualization-mode-heatmap").getAttribute("aria-checked")).toBe("false");
  });
});
