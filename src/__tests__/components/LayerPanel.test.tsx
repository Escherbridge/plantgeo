import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

import { LayerPanel } from "@/components/map/layer-panel/LayerPanel";
import {
  PANEL_FIXED_ROW,
  PANEL_SCROLLER,
  PANEL_SHELL,
} from "@/components/map/layer-panel/panel-scroll";
import { LAYER_REGISTRY, LAYER_TOGGLE_IDS } from "@/lib/map/layer-registry";
import { DEFAULT_SOIL_FIELD_DEPTHS } from "@/lib/environmental/soil-field";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import { useSoilStore } from "@/stores/soil-store";
import { useVegetationStore } from "@/stores/vegetation-store";

/**
 * Every class that makes an element a scroll container. `overflow-y-auto` is in here on its
 * own because that is the whole point: per CSS Overflow 3 §3.1, setting one axis to something
 * other than `visible` makes the OTHER axis compute to `auto` too -- which is how the old icon
 * rail ended up with a horizontal scrollbar nobody asked for.
 */
const SCROLLING_CLASS = /\boverflow-(x|y)-(auto|scroll)\b|\boverflow-(auto|scroll)\b/;

/** `className` is an SVGAnimatedString on the icon elements, so read the attribute instead. */
function classListOf(element: Element): string {
  return element.getAttribute("class") ?? "";
}

function openPanel(): HTMLElement {
  act(() => {
    usePanelStore.getState().toggleLayerPanel();
  });
  return screen.getByTestId("layer-panel");
}

function rowFor(layerId: string): HTMLElement {
  return screen.getByTestId(`layer-row-${layerId}`);
}

beforeEach(() => {
  useMapStore.setState({ activeLayers: [] });
  useLayerStore.setState({ legendVisible: true, layerOpacity: {} });
  usePanelStore.setState({ layerPanelOpen: false });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
  useVegetationStore.setState({ mode: "ndvi", ndviMode: "absolute", showNDWI: false });
});

describe("LayerPanel scroll contract", () => {
  /**
   * The Job A regression, made permanent.
   *
   * The rail this panel joins had `overflow-y-auto` on an absolutely-positioned box with no
   * width, so its shrink-to-fit width was its widest child (a 44px button) and a badge offset
   * `-right-0.5` pushed the scrollable region 2px past it. Chrome drew a full horizontal
   * scrollbar across a 44px column for those 2px, appearing the moment the first data layer
   * was switched on.
   */
  it("has exactly one scrolling descendant", () => {
    renderWithProviders(<LayerPanel />);
    const panel = openPanel();

    const scrollers = Array.from(panel.querySelectorAll("*")).filter((element) =>
      SCROLLING_CLASS.test(classListOf(element))
    );

    expect(scrollers).toHaveLength(1);
    expect(classListOf(scrollers[0])).toContain("min-h-0");
  });

  /**
   * `min-h-0` on the scroller and `shrink-0` on everything else are a pair, and neither works
   * alone. Without `min-h-0` a flex child's default `min-height` is its content, so the
   * scroller stretches the shell and hands you a second scrollbar; without `shrink-0` the
   * fixed rows squash toward their own content instead of letting the scroller take the
   * squeeze -- which is exactly how the rail silently lost its 44px tap target.
   */
  it("gives every non-scrolling direct child of the shell shrink-0", () => {
    renderWithProviders(<LayerPanel />);
    const panel = openPanel();

    for (const child of Array.from(panel.children)) {
      const classes = classListOf(child);
      if (SCROLLING_CLASS.test(classes)) {
        expect(classes).toContain("min-h-0");
        expect(classes).toContain("flex-1");
      } else {
        expect(classes, child.tagName).toContain("shrink-0");
      }
    }
  });

  // The shell itself must never scroll: it is anchored to the container's edges, so anything
  // that overflows belongs to the one scroller inside it.
  it("keeps the shell itself out of the scroll container business", () => {
    expect(PANEL_SHELL).toContain("overflow-hidden");
    expect(SCROLLING_CLASS.test(PANEL_SHELL)).toBe(false);
    expect(PANEL_FIXED_ROW).toContain("shrink-0");
    // Padding for any negatively-offset decoration, so it lands inside the padding box rather
    // than extending the scrollable region on the inline axis.
    expect(PANEL_SCROLLER).toContain("pr-1");
    expect(PANEL_SCROLLER).toContain("overscroll-contain");
  });

  // Height derives from the container, not from `vh`: the map shell is sized in `dvh`, and on
  // a mobile browser with a retracting toolbar a `vh` cap can exceed the box it is capping.
  it("takes its height from the container rather than the viewport", () => {
    expect(PANEL_SHELL).not.toMatch(/\bmax-h-\[.*vh/);
    expect(PANEL_SHELL).toContain("top-20");
    expect(PANEL_SHELL).toContain("bottom-4");
  });
});

describe("LayerPanel layer tree", () => {
  it("renders one row per registry layer, named from the registry", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    for (const toggleId of LAYER_TOGGLE_IDS) {
      const row = rowFor(toggleId);
      expect(row.textContent, toggleId).toContain(LAYER_REGISTRY[toggleId].label);
    }
  });

  // `building-footprints` is switched from the MapControls toolbar and belongs to no panel, so
  // without a home in the tree the one comprehensive list of layers would be missing one.
  it("files the layer no panel governs under its own group", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    const basemap = screen.getByTestId("layer-group-Basemap");
    expect(basemap.textContent).toContain(LAYER_REGISTRY["building-footprints"].label);
  });

  it("writes the same activeLayers the panel switches write", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    fireEvent.click(screen.getByRole("switch", { name: "Show Sensor Stations on map" }));

    expect(useMapStore.getState().activeLayers).toContain("sensors");
  });

  it("reflects a layer switched on from anywhere else", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();
    const eye = screen.getByRole("switch", { name: "Show Sensor Stations on map" });
    expect(eye.getAttribute("aria-checked")).toBe("false");

    act(() => {
      useMapStore.getState().toggleLayer("sensors");
    });

    expect(eye.getAttribute("aria-checked")).toBe("true");
  });

  // Governance, not a rendering accident: a withheld layer reads false in useLayerVisibility
  // whatever activeLayers says, so its eye must be disabled rather than merely off.
  it("disables the eye of a withheld layer and says why", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    const row = rowFor("building-footprints");
    const eye = row.querySelector<HTMLButtonElement>('[role="switch"]');
    expect(eye?.disabled).toBe(true);
    expect(row.textContent).toContain("3D building footprints are not published yet");
  });

  /**
   * A control that adjusts nothing is the fabricated affordance layer-legends.ts exists to
   * prevent, so the slider appears only once the layer is actually drawing.
   */
  it("offers no opacity slider until the layer is switched on", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    expect(rowFor("sensors").querySelector('input[type="range"]')).toBeNull();

    act(() => {
      useMapStore.getState().toggleLayer("sensors");
    });

    const slider = rowFor("sensors").querySelector<HTMLInputElement>('input[type="range"]');
    expect(slider).not.toBeNull();
    // Named and spoken: a bare range announces as an unnamed slider whose value reads "0.6".
    expect(slider?.getAttribute("aria-label")).toBe("Sensor Stations opacity");
    expect(slider?.getAttribute("aria-valuetext")).toBe("100 percent");
  });

  // Opacity 0 is unreachable by construction. An opacity-0 layer is still hit-tested by
  // queryRenderedFeatures, so it would swallow clicks meant for the ground beneath it; the
  // eye, which sets layout visibility, is how a layer is turned off.
  it("floors the slider above zero and caps it at 'as authored'", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();
    act(() => {
      useMapStore.getState().toggleLayer("sensors");
    });

    const slider = rowFor("sensors").querySelector<HTMLInputElement>('input[type="range"]');
    expect(Number(slider?.min)).toBeGreaterThan(0);
    expect(Number(slider?.max)).toBe(1);
    expect(Number(slider?.step)).toBeGreaterThan(0);
  });

  /**
   * The three soil toggles shared one `soil-store.opacity` until 2026-08-08, which meant
   * dimming the SoilGrids raster necessarily dimmed both ERA5-Land measurements.
   */
  it("keeps each layer's opacity to itself", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    act(() => {
      useLayerStore.getState().setLayerOpacity("soil-moisture", 0.5);
    });

    const opacity = useLayerStore.getState().layerOpacity;
    expect(opacity["soil-moisture"]).toBe(0.5);
    expect(opacity["soil-temperature"]).toBeUndefined();
    expect(opacity.soil).toBeUndefined();
    expect(rowFor("soil-moisture").textContent).toContain("50%");
    expect(rowFor("soil-temperature").textContent).toContain("100%");
  });

  /**
   * The group eye governs several switches, so "mixed" is the only honest reading of a
   * partially-on category. Withheld layers are excluded from the count -- reporting "0 of 1"
   * for a layer nobody can switch on would describe a gap that is really a decision.
   */
  it("reports a partly-on group as mixed and clears it in one click", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();
    const groupEye = screen.getByRole("switch", { name: "Show all Water layers on map" });
    expect(groupEye.getAttribute("aria-checked")).toBe("false");

    act(() => {
      useMapStore.getState().toggleLayer("sensors");
    });
    expect(groupEye.getAttribute("aria-checked")).toBe("mixed");

    fireEvent.click(groupEye);

    expect(useMapStore.getState().activeLayers).not.toContain("sensors");
    expect(groupEye.getAttribute("aria-checked")).toBe("false");
  });

  it("turns a whole group on when none of it is on", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    fireEvent.click(screen.getByRole("switch", { name: "Show all Water layers on map" }));

    const active = useMapStore.getState().activeLayers;
    for (const toggleId of ["water", "drought", "weather", "sensors", "watersheds"]) {
      expect(active, toggleId).toContain(toggleId);
    }
  });

  // One boolean, two controls: the corner legend card and this header button. Two surfaces
  // over one value cannot contradict each other.
  it("shares the legend's own visibility flag rather than keeping a second one", () => {
    renderWithProviders(<LayerPanel />);
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "Hide legend entries" }));

    expect(useLayerStore.getState().legendVisible).toBe(false);
  });

  it("renders nothing while it is undocked", () => {
    renderWithProviders(<LayerPanel />);

    expect(screen.queryByTestId("layer-panel")).toBeNull();
  });
});
