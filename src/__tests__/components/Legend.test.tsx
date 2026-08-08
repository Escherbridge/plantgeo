import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

import { Legend } from "@/components/map/Legend";
import { DROUGHT_COLORS } from "@/components/map/layers/DroughtLayer";
import { CONDITION_COLORS } from "@/components/map/layers/WaterLayer";
import {
  DEFAULT_SOIL_FIELD_DEPTHS,
  SOIL_FIELD_MEASURES,
} from "@/lib/environmental/soil-field";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import { useVegetationStore } from "@/stores/vegetation-store";

/**
 * jsdom re-serialises every colour it parses ("#ffff00" reads back as "rgb(255, 255, 0)"),
 * so an imported constant is compared through the SAME parser rather than as a string.
 */
function asRenderedColor(color: string): string {
  const probe = document.createElement("div");
  probe.style.backgroundColor = color;
  return probe.style.backgroundColor;
}

/** The decorative colour carriers of one entry, in document order. */
function swatchColorsOf(entry: HTMLElement): string[] {
  return Array.from(
    entry.querySelectorAll<HTMLElement>('span[aria-hidden="true"]')
  ).map((swatch) => swatch.style.backgroundColor);
}

function toggleLayerOn(toggleId: string): void {
  act(() => {
    useMapStore.getState().toggleLayer(toggleId);
  });
}

beforeEach(() => {
  useMapStore.setState({ activeLayers: [] });
  useLayerStore.setState({ legendVisible: true });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
  useVegetationStore.setState({ mode: "ndvi", ndviMode: "absolute", showNDWI: false });
});

describe("Legend", () => {
  it("renders nothing while every layer is off", () => {
    renderWithProviders(<Legend />);

    // The map opens with an empty activeLayers, so the card must not claim the corner
    // before anything is drawn.
    expect(screen.queryByTestId("legend")).toBeNull();
  });

  it("legends a layer once its toggle is on, in the colours its renderer paints", () => {
    renderWithProviders(<Legend />);
    toggleLayerOn("drought");

    const entry = screen.getByTestId("legend-entry-drought");
    expect(entry).toBeTruthy();
    expect(entry.textContent).toContain("D0 — Abnormally dry");
    expect(entry.textContent).toContain("D4 — Exceptional drought");

    // The five drawn USDM classes, read from the record DroughtLayer's fill expression
    // keys to -- so a palette edit that missed the legend would fail here.
    expect(swatchColorsOf(entry)).toEqual(
      [
        DROUGHT_COLORS.D0,
        DROUGHT_COLORS.D1,
        DROUGHT_COLORS.D2,
        DROUGHT_COLORS.D3,
        DROUGHT_COLORS.D4,
      ].map(asRenderedColor)
    );
  });

  it("keeps one section per active layer and drops it when the layer goes off", () => {
    renderWithProviders(<Legend />);
    toggleLayerOn("drought");
    toggleLayerOn("water");

    expect(screen.getByTestId("legend-entry-drought")).toBeTruthy();
    const water = screen.getByTestId("legend-entry-water");
    // Gauges are coloured by condition; the diverging ramp's neutral pivot is "Normal".
    expect(swatchColorsOf(water)).toContain(asRenderedColor(CONDITION_COLORS.normal));
    expect(water.textContent).toContain("Groundwater wells");

    toggleLayerOn("drought");

    expect(screen.queryByTestId("legend-entry-drought")).toBeNull();
    expect(screen.getByTestId("legend-entry-water")).toBeTruthy();
  });

  it("collapses to the header row and back", () => {
    renderWithProviders(<Legend />);
    toggleLayerOn("drought");

    fireEvent.click(screen.getByLabelText("Hide legend entries"));

    expect(screen.queryByTestId("legend-entry-drought")).toBeNull();
    // The card itself stays: collapsing hides the entries, it does not unmount the legend.
    expect(screen.getByTestId("legend")).toBeTruthy();
    expect(screen.getByText("Legend")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Show legend entries"));

    expect(screen.getByTestId("legend-entry-drought")).toBeTruthy();
  });

  it("resolves a mode-dependent entry from the panel's selected depth", () => {
    renderWithProviders(<Legend />);
    toggleLayerOn("soil-moisture");

    const moisture = SOIL_FIELD_MEASURES.moisture;
    const entry = screen.getByTestId("legend-entry-soil-moisture");
    expect(entry.textContent).toContain(
      `${moisture.quantityLabel} (${moisture.unitLabel})`
    );
    expect(entry.textContent).toContain("Surface (0-7 cm)");

    act(() => {
      useSoilStore.getState().setFieldDepth("moisture", "deep");
    });

    expect(screen.getByTestId("legend-entry-soil-moisture").textContent).toContain(
      "Deep (28-100 cm)"
    );
  });

  it("paints its ramp from the band table the soil field is drawn with", () => {
    renderWithProviders(<Legend />);
    toggleLayerOn("soil-temperature");

    const bar = screen
      .getByTestId("legend-entry-soil-temperature")
      .querySelector<HTMLElement>('span[aria-hidden="true"]');
    const gradient = bar?.style.backgroundImage ?? "";

    // jsdom normalises the colours inside the gradient too, so each band colour is looked
    // for in the same serialisation.
    for (const band of SOIL_FIELD_MEASURES.temperature.bands) {
      expect(gradient).toContain(asRenderedColor(band.color));
    }
  });

  it("gives no section to a switched-on layer that paints nothing", () => {
    renderWithProviders(<Legend />);
    // SoilGrids has no published first-party raster, so SoilLayer adds no source at all --
    // legending it would invent an encoding the map never draws.
    toggleLayerOn("soil");

    expect(screen.queryByTestId("legend")).toBeNull();

    // NDWI is the same story one level down: the mode exists, the tiles never do.
    //
    // Driven with setState rather than a setter because the store deliberately has none:
    // NDWI, NBR and the NDVI anomaly have no published upstream at all, so `mode` is a
    // constant the renderer still branches on rather than a control a reader can reach, and
    // its setter existed only to be called from permanently disabled checkboxes. The renderer
    // branch is still real, which is what this case exercises.
    toggleLayerOn("vegetation");
    act(() => {
      useVegetationStore.setState({ mode: "ndwi" });
    });

    expect(screen.queryByTestId("legend-entry-vegetation")).toBeNull();

    act(() => {
      useVegetationStore.setState({ mode: "ndvi" });
    });

    expect(screen.getByTestId("legend-entry-vegetation")).toBeTruthy();
  });
});
