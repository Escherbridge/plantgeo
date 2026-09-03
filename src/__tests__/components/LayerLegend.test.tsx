import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

import { LayerLegend } from "@/components/map/layer-panel/LayerLegend";
import { DROUGHT_COLORS } from "@/components/map/layers/DroughtLayer";
import {
  GAUGE_READING_COLORS,
  WATER_CELL_MEAN_FLOW_COLOR_STOPS,
} from "@/components/map/layers/WaterLayer";
import { FIRE_CELL_NOT_A_PERIMETER_NOTE } from "@/lib/map/fire-cell-caption";
import { WATER_CELL_AGGREGATE_NOTE } from "@/lib/map/water-cell-caption";
import { LAYER_RENDER_CONTRACT } from "@/lib/map/layer-render-contract";
import {
  DEFAULT_SOIL_FIELD_DEPTHS,
  SOIL_FIELD_MEASURES,
} from "@/lib/environmental/soil-field";
import { LAYER_REGISTRY } from "@/lib/map/layer-registry";
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

/** The chip row, which is the legend's collapsed state and its only always-visible part. */
function chipRow(): HTMLElement {
  return screen.getByTestId("layer-legend-chips");
}

beforeEach(() => {
  useMapStore.setState({ activeLayers: [] });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
  useVegetationStore.setState({ mode: "ndvi", ndviMode: "absolute", showNDWI: false });
});

/**
 * The legend and the active-layer chips are ONE component, which is what these cases are
 * really about: every assertion below reads the collapsed chips and the expanded taxonomy off
 * the same mount, so a divergence between them is not expressible rather than merely untested.
 */
describe("LayerLegend", () => {
  it("renders nothing while every layer is off", () => {
    renderWithProviders(<LayerLegend />);

    // The map opens with an empty activeLayers, so this must not claim the corner before
    // anything is drawn.
    expect(screen.queryByTestId("layer-legend")).toBeNull();
  });

  it("gives a drawn layer a chip named the way the layer tree names it", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");

    const chip = screen.getByTestId("legend-chip-drought");
    // The registry label, not the encoding's title: a chip names the LAYER, and naming it
    // anything else would be a second vocabulary for a layer's name.
    expect(chip.textContent).toContain(LAYER_REGISTRY.drought.label);
    // The chips are the accessible name of the row, so a screen reader hears the same list a
    // sighted reader sees rather than "button, collapsed".
    expect(chipRow().getAttribute("aria-label")).toContain(LAYER_REGISTRY.drought.label);
  });

  it("keeps the taxonomy shut until it is asked for", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");

    expect(chipRow().getAttribute("aria-expanded")).toBe("false");
    // A collapsed disclosure must not claim to control a region that is not in the document.
    expect(chipRow().hasAttribute("aria-controls")).toBe(false);
    expect(screen.queryByTestId("layer-legend-taxonomy")).toBeNull();
  });

  it("expands the taxonomy on click, and names it while it exists", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");

    fireEvent.click(chipRow());

    expect(chipRow().getAttribute("aria-expanded")).toBe("true");
    const taxonomyId = chipRow().getAttribute("aria-controls") ?? "";
    expect(document.getElementById(taxonomyId)).toBe(
      screen.getByTestId("layer-legend-taxonomy")
    );
  });

  // Hover and click are two of the three ways in, and pointer-enter is the one a mouse has.
  // `pointerType` is read because a touch reports an enter that never leaves, which would pin
  // the panel open with no way back -- on touch the click handler owns it.
  it("expands the taxonomy on hover and closes it again on leave", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");
    const container = screen.getByTestId("layer-legend");

    fireEvent.pointerEnter(container, { pointerType: "mouse" });
    expect(screen.getByTestId("layer-legend-taxonomy")).toBeTruthy();

    fireEvent.pointerLeave(container);
    expect(screen.queryByTestId("layer-legend-taxonomy")).toBeNull();
  });

  // The keyboard's equivalent of hover, and the way out of a pinned panel for a reader who has
  // no pointer to move away.
  it("expands on focus and closes on Escape", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");

    fireEvent.focus(chipRow());
    expect(screen.getByTestId("layer-legend-taxonomy")).toBeTruthy();

    fireEvent.click(chipRow());
    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByTestId("layer-legend-taxonomy")).toBeNull();
  });

  it("legends a layer in the colours its renderer paints", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");
    fireEvent.click(chipRow());

    const entry = screen.getByTestId("legend-entry-drought");
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

  /** The taxonomy the owner asked for: the same category headings the layer tree uses. */
  it("groups the expanded entries under their registry categories", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");
    toggleLayerOn("fire");
    fireEvent.click(chipRow());

    const taxonomy = screen.getByTestId("layer-legend-taxonomy");
    expect(taxonomy.textContent).toContain("Water");
    expect(taxonomy.textContent).toContain("Fire");
  });

  it("keeps one chip and one section per active layer, dropping both when it goes off", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("drought");
    toggleLayerOn("water");
    fireEvent.click(chipRow());

    expect(screen.getByTestId("legend-chip-drought")).toBeTruthy();
    expect(screen.getByTestId("legend-entry-drought")).toBeTruthy();
    const water = screen.getByTestId("legend-entry-water");
    // Gauges are coloured by whether they reported a discharge value -- the five-class
    // condition ramp this replaced was unreachable, since NWIS carries no percentile.
    expect(swatchColorsOf(water)).toContain(
      asRenderedColor(GAUGE_READING_COLORS.reporting)
    );
    expect(water.textContent).toContain("Groundwater wells");

    toggleLayerOn("drought");

    // Both halves of the one component drop the layer together, because both read one array.
    expect(screen.queryByTestId("legend-chip-drought")).toBeNull();
    expect(screen.queryByTestId("legend-entry-drought")).toBeNull();
    expect(screen.getByTestId("legend-chip-water")).toBeTruthy();
    expect(screen.getByTestId("legend-entry-water")).toBeTruthy();
  });

  // The three sections the polygon rendering added. Each says in words what the shapes cannot:
  // a filled square under `fire` or `water` is in the same visual language as `fire-perimeters`
  // and `burn-severity`, and only the caption distinguishes an aggregate from a real extent.
  it("tells the reader a fire cell is a density, not a perimeter", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("fire");
    fireEvent.click(chipRow());

    const fire = screen.getByTestId("legend-entry-fire");
    expect(fire.textContent).toContain(FIRE_CELL_NOT_A_PERIMETER_NOTE);
    // Both encodings of the one cell are named by the shape each describes, because the legend
    // has no camera to switch on.
    expect(fire.textContent).toContain("Cell fill (zoomed out): detections in the cell");
    expect(fire.textContent).toContain("Dot colour (zoomed in)");
  });

  it("legends the coarse water cell in its own colours and calls it a mean", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("water");
    fireEvent.click(chipRow());

    const water = screen.getByTestId("legend-entry-water");
    expect(water.textContent).toContain("Cell fill (zoomed out): mean discharge");
    expect(water.textContent).toContain(WATER_CELL_AGGREGATE_NOTE);
    // A ramp is a gradient bar with captioned ends, not swatches, so the axis is what proves the
    // legend is reading the renderer's own stops rather than restating a scale.
    const stops = WATER_CELL_MEAN_FLOW_COLOR_STOPS;
    expect(water.textContent).toContain(stops[0].label);
    expect(water.textContent).toContain(stops[stops.length - 1].label);
  });

  it("states vegetation's measured support from the contract rather than a written 0.25", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("vegetation");
    fireEvent.click(chipRow());

    const declared = LAYER_RENDER_CONTRACT.vegetation.declaredSupportDegrees;
    expect(screen.getByTestId("legend-entry-vegetation").textContent).toContain(
      "Each cell is one measured " + declared + "° sample"
    );
  });

  it("resolves a mode-dependent entry from the manager's selected depth", () => {
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("soil-moisture");
    fireEvent.click(chipRow());

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
    renderWithProviders(<LayerLegend />);
    toggleLayerOn("soil-temperature");
    fireEvent.click(chipRow());

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

  it("gives no chip and no section to a switched-on layer that paints nothing", () => {
    renderWithProviders(<LayerLegend />);
    // SoilGrids has no published first-party raster, so SoilLayer adds no source at all --
    // legending it would invent an encoding the map never draws.
    toggleLayerOn("soil");

    expect(screen.queryByTestId("layer-legend")).toBeNull();

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

    expect(screen.queryByTestId("legend-chip-vegetation")).toBeNull();

    act(() => {
      useVegetationStore.setState({ mode: "ndvi" });
    });

    expect(screen.getByTestId("legend-chip-vegetation")).toBeTruthy();
  });
});
