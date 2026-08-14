import type { ReactElement } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { httpBatchLink } from "@trpc/client";
import superjson from "superjson";
import { renderWithProviders } from "@/test/utils";
import { trpc } from "@/lib/trpc/client";

import { DockSections } from "@/components/map/layer-panel/DockSections";
import { SearchDockSection } from "@/components/map/layer-panel/SearchDockSection";
import { ViewDockSection, VIEW_SECTION_LABEL } from "@/components/map/layer-panel/ViewDockSection";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import { useSoilStore } from "@/stores/soil-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { DEFAULT_SOIL_FIELD_DEPTHS } from "@/lib/environmental/soil-field";

/**
 * The 44px mobile tap-target floor: `min-h-11`/`h-11 w-11` is 44px, and every interactive
 * element in the map manager grows to it under `max-sm:*`. `LayerPanel.test.tsx` already
 * covers the manager's own shell (`PANEL_SHELL` becoming a full-screen overlay); this file goes
 * one layer deeper, into the three sections that shell composes -- the layer tree, the search
 * box, and the render-mode panel -- each mounted standalone the same way `LayerPanel` composes
 * them (see `src/components/map/layer-panel/LayerPanel.tsx`).
 */

/** `DockSections`' `UnreadAlertBadge` reads through tRPC, so every render here needs a client -- the same wrapping `LayerPanel.test.tsx` uses for the same reason. */
function renderWithTrpc(ui: ReactElement) {
  const trpcClient = trpc.createClient({
    links: [httpBatchLink({ url: "http://localhost/api/trpc", transformer: superjson })],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithProviders(
    <trpc.Provider client={trpcClient} queryClient={queryClient}>
      {ui}
    </trpc.Provider>
  );
}

beforeEach(() => {
  useMapStore.setState({ activeLayers: [] });
  useLayerStore.setState({ layerOpacity: {} });
  usePanelStore.setState({
    layerPanelOpen: false,
    expandedDetails: ["search"],
    pendingScrollSection: null,
  });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
  useTimeSliderStore.setState({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,
  });
  useVegetationStore.setState({ mode: "ndvi", ndviMode: "absolute", showNDWI: false });
});

describe("DockSections mobile layout (the sidebar's layer tree)", () => {
  it("gives every category's disclosure header and group-toggle switch the 44px mobile floor", () => {
    renderWithTrpc(<DockSections />);

    // "fire" is a stable registry category (see GROUP_LABELS in dock-sections.ts) and its
    // group section is always present, seeded open by default (LayerGroupSection isExpanded
    // starts true), so both controls are in the DOM with no interaction required. The
    // group-toggle-all control is `role="switch"`, same as each row's own eye toggle, so it is
    // addressed by its aria-label rather than by position among the section's plain buttons.
    const group = screen.getByTestId("layer-group-fire");
    // The header's disclosure control is the first plain button in DOM order; the group's
    // report toggle (DetailsSection, filed at the bottom of the same section) is also a plain
    // button and would otherwise collide on name matching, so position -- fixed by the JSX
    // order in LayerGroupSection -- is the unambiguous way to reach it.
    const disclosureButton = within(group).getAllByRole("button")[0];
    const groupToggleSwitch = within(group).getByRole("switch", {
      name: "Show all Fire layers on map",
    });

    expect(disclosureButton.className).toContain("max-sm:min-h-11");
    expect(groupToggleSwitch.className).toContain("max-sm:h-11");
    expect(groupToggleSwitch.className).toContain("max-sm:w-11");
  });
});

describe("SearchDockSection mobile layout (the search box)", () => {
  it("grows the coordinate-jump result to the 44px mobile floor", () => {
    renderWithTrpc(<SearchDockSection />);

    const input = screen.getByLabelText("Search places") as HTMLInputElement;
    // A coordinate pair renders the "Go to lat, lon" button synchronously -- parseCoordinatePair
    // is pure and needs no debounce or network round trip, unlike a place-name search.
    fireEvent.change(input, { target: { value: "43.6, -116.2" } });

    const jumpButton = screen.getByRole("button", { name: /Go to 43\.6000, -116\.2000/ });
    expect(jumpButton.className).toContain("max-sm:min-h-11");
  });
});

describe("ViewDockSection mobile layout (the render-mode panel)", () => {
  it("grows the basemap swatches and render-mode switches to the 44px mobile floor", () => {
    renderWithTrpc(<ViewDockSection />);

    // "view" is not in INITIALLY_EXPANDED_SECTIONS, so its body is collapsed until its own
    // disclosure header is opened -- unlike "search", which the beforeEach above seeds open.
    fireEvent.click(screen.getByRole("button", { name: VIEW_SECTION_LABEL }));

    const terrainSwitch = screen.getByRole("switch", { name: "Terrain" });
    expect(terrainSwitch.className).toContain("max-sm:min-h-11");

    const basemapSwatch = screen.getByRole("button", { name: "Dark basemap" });
    expect(basemapSwatch.className).toContain("max-sm:h-11");
    expect(basemapSwatch.className).toContain("max-sm:w-11");
  });
});
