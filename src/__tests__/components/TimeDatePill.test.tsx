import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import TimeDatePill from "@/components/map/TimeDatePill";
import { usePanelStore } from "@/stores/panel-store";
import { UNINITIALIZED_DATE, useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";

const SERVER_CURRENT_DATE = "2026-08-04";

/**
 * No tRPC here at all, deliberately: the pill reads the store and nothing else. The fetch it
 * used to own moved to TimeSliderCapabilitiesLoader when the scrubber moved into the dock
 * (2026-08-08), which is what makes this a pure render test.
 */
const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 30,
  layers: [
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2026-06-17",
    },
  ],
};

beforeEach(() => {
  useTimeSliderStore.setState({
    selectedDate: SERVER_CURRENT_DATE,
    forecastVariant: "monte_carlo",
    capabilities: CAPABILITIES,
    capabilitiesUnavailable: false,
    focusedLayerName: null,
  });
  usePanelStore.setState({
    layerPanelOpen: false,
    expandedDetails: [],
    pendingScrollSection: null,
  });
});

describe("TimeDatePill", () => {
  it("renders nothing until there is a day to claim", () => {
    useTimeSliderStore.setState({
      capabilities: null,
      selectedDate: UNINITIALIZED_DATE,
    });
    renderWithProviders(<TimeDatePill />);

    // A placeholder here would claim a date nobody chose; the payload is the only source of one.
    expect(screen.queryByTestId("map-date-pill")).toBeNull();
    expect(screen.queryByTestId("time-slider-toggle")).toBeNull();
  });

  it("states the map's day, and says nothing more while it is the live one", () => {
    renderWithProviders(<TimeDatePill />);

    expect(screen.getByTestId("time-slider-toggle-date").textContent).toBe(SERVER_CURRENT_DATE);
    // Silent at the live default: a chip on every day would stop meaning anything.
    expect(screen.queryByText("Past day")).toBeNull();
    expect(screen.queryByText("Beyond record")).toBeNull();
    expect(screen.queryByTestId("time-slider-bar-today")).toBeNull();
  });

  it("flags an off-today day and offers a one-tap way back", () => {
    renderWithProviders(<TimeDatePill />);
    // An off-today date silently filters EVERY layer, so the way back must cost no panel open.
    act(() => useTimeSliderStore.getState().setSelectedDate("2026-08-02"));

    expect(screen.getByText("Past day")).toBeTruthy();
    fireEvent.click(screen.getByTestId("time-slider-bar-today"));

    expect(useTimeSliderStore.getState().selectedDate).toBe(SERVER_CURRENT_DATE);
    expect(screen.queryByTestId("time-slider-bar-today")).toBeNull();
    expect(screen.queryByText("Past day")).toBeNull();
  });

  it("distinguishes a past observed day from one past the end of the record", () => {
    renderWithProviders(<TimeDatePill />);
    act(() => useTimeSliderStore.getState().setSelectedDate("2026-08-20"));

    expect(screen.getByText("Beyond record")).toBeTruthy();
    expect(screen.queryByText("Past day")).toBeNull();
  });

  it("opens the dock at the Time section rather than expanding anything itself", () => {
    renderWithProviders(<TimeDatePill />);

    const pill = screen.getByTestId("time-slider-toggle");
    // No longer a disclosure: there is one scrubber now, in the dock, so an aria-expanded here
    // would be a claim about a region this button does not contain.
    expect(pill.hasAttribute("aria-expanded")).toBe(false);
    expect(pill.getAttribute("aria-label")).toBe("Open time controls");

    fireEvent.click(pill);

    // The same three-fact handshake the toolbar's alert bell uses: dock it, expand it, scroll
    // to it. Expanding a section without docking would point at something nobody can see.
    const panelState = usePanelStore.getState();
    expect(panelState.layerPanelOpen).toBe(true);
    expect(panelState.expandedDetails).toContain("time");
    expect(panelState.pendingScrollSection).toBe("time");
  });

  it("names an outright capabilities failure instead of rendering nothing", () => {
    useTimeSliderStore.setState({
      capabilities: null,
      selectedDate: UNINITIALIZED_DATE,
      capabilitiesUnavailable: true,
    });
    renderWithProviders(<TimeDatePill />);

    // A failed request and a slow-but-healthy one must not look identical -- the former was
    // mistaken for "no slider UI" once already.
    expect(screen.getByTestId("time-slider-toggle").textContent).toContain(
      "Time range unavailable"
    );
    // And it still opens the section, where TimeSlider's full notice explains the failure.
    fireEvent.click(screen.getByTestId("time-slider-toggle"));
    expect(usePanelStore.getState().expandedDetails).toContain("time");
  });

  it("floats over the canvas without swallowing map drags", () => {
    renderWithProviders(<TimeDatePill />);

    const row = screen.getByTestId("map-date-pill");
    // The row is a box over the map; without the pass-through its padding would eat every drag
    // that starts under it. Each control opts back in through TOP_BAR_CONTROL_CLASSES.
    expect(row.className).toContain("pointer-events-none");
    expect(screen.getByTestId("time-slider-toggle").className).toContain("pointer-events-auto");
    // It is one row of controls now, not the 24rem scrolling column it replaced.
    expect(row.className).not.toMatch(/overflow-/);
  });
});
