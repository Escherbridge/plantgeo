import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { httpBatchLink } from "@trpc/client";
import superjson from "superjson";
import { renderWithProviders } from "@/test/utils";
import { trpc } from "@/lib/trpc/client";

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

/**
 * The dock inside a tRPC provider, because its Alerts row reads the unread count from
 * `alerts.getUnreadCount` -- the same query the toolbar bell observes, so the badge and the
 * bell can never disagree. The query is disabled without a session cookie, and jsdom has
 * none, so nothing here reaches the link.
 *
 * Wrapped here rather than in `renderWithProviders`: several suites replace
 * `@/lib/trpc/client` wholesale with `vi.mock`, and a shared helper importing `trpc` would
 * break in every one of them.
 */
function renderDock() {
  const trpcClient = trpc.createClient({
    // superjson because the router is built with it: an untransformed link is a type error,
    // and would decode a real response wrongly if one ever arrived.
    links: [httpBatchLink({ url: "http://localhost/api/trpc", transformer: superjson })],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithProviders(
    <trpc.Provider client={trpcClient} queryClient={queryClient}>
      <LayerPanel />
    </trpc.Provider>
  );
}

function openPanel(): HTMLElement {
  act(() => {
    usePanelStore.getState().toggleLayerPanel();
  });
  return screen.getByTestId("layer-panel");
}

function detailsToggleFor(label: string): HTMLElement {
  return screen.getByRole("button", { name: label });
}

function rowFor(layerId: string): HTMLElement {
  return screen.getByTestId(`layer-row-${layerId}`);
}

beforeEach(() => {
  useMapStore.setState({ activeLayers: [] });
  useLayerStore.setState({ legendVisible: true, layerOpacity: {} });
  usePanelStore.setState({
    layerPanelOpen: false,
    expandedDetails: [],
    pendingScrollSection: null,
  });
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
    renderDock();
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
    renderDock();
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
    renderDock();
    openPanel();

    for (const toggleId of LAYER_TOGGLE_IDS) {
      const row = rowFor(toggleId);
      expect(row.textContent, toggleId).toContain(LAYER_REGISTRY[toggleId].label);
    }
  });

  // `building-footprints` is switched from the MapControls toolbar and belongs to no panel, so
  // without a home in the tree the one comprehensive list of layers would be missing one.
  it("files the layer no panel governs under its own group", () => {
    renderDock();
    openPanel();

    const basemap = screen.getByTestId("layer-group-Basemap");
    expect(basemap.textContent).toContain(LAYER_REGISTRY["building-footprints"].label);
  });

  // `map-store.activeLayers` is the single source of layer visibility, and since the dock
  // absorbed the sheets it is also the only surface writing it.
  it("writes layer visibility to activeLayers and nowhere else", () => {
    renderDock();
    openPanel();

    fireEvent.click(screen.getByRole("switch", { name: "Show Sensor Stations on map" }));

    expect(useMapStore.getState().activeLayers).toContain("sensors");
  });

  it("reflects a layer switched on from anywhere else", () => {
    renderDock();
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
    renderDock();
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
    renderDock();
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
    renderDock();
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
    renderDock();
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
    renderDock();
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
    renderDock();
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
    renderDock();
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "Hide legend entries" }));

    expect(useLayerStore.getState().legendVisible).toBe(false);
  });

  it("renders nothing while it is undocked", () => {
    renderDock();

    expect(screen.queryByTestId("layer-panel")).toBeNull();
  });
});

/**
 * The dock absorbed seven right-hand sheets on 2026-08-08, and the property that made that
 * affordable is the one these cases pin: a report is MOUNTED only while its own disclosure is
 * open, so opening the dock costs the layer rows and nothing else.
 *
 * The two carets in this panel are not the same control and must not be conflated. A group's
 * caret shows layer rows, which is free and therefore open by default; a report's caret mounts
 * a component with its own warehouse queries, and is closed until asked for.
 */
describe("LayerPanel dock sections", () => {
  it("collapses every report while showing every layer row", () => {
    renderDock();
    openPanel();

    // The layer list is fully expanded...
    expect(rowFor("sensors")).toBeTruthy();
    // ...and not one report is mounted with it. Eight expanded reports on every dock open
    // would issue eight panels' worth of warehouse queries before anyone asked a question.
    expect(usePanelStore.getState().expandedDetails).toEqual([]);
    for (const section of ["fire", "water", "vegetation", "soil", "community", "alerts"]) {
      const disclosure = screen
        .getByTestId(`dock-section-${section}`)
        .querySelector("button");
      expect(disclosure?.getAttribute("aria-expanded"), section).toBe("false");
      // The DOM-level statement of "not mounted": the body carries its own testid, queried
      // directly rather than through `aria-controls`, which names nothing while collapsed
      // (see the next assertion).
      expect(screen.queryByTestId(`dock-section-body-${section}`), section).toBeNull();
      // A collapsed disclosure must not claim to control a region that is not in the
      // document -- that dangling reference is exactly what an assistive technology cannot
      // resolve.
      expect(disclosure?.hasAttribute("aria-controls"), section).toBe(false);
    }
  });

  it("names its body with aria-controls once expanded, and only once expanded", () => {
    renderDock();
    openPanel();

    const disclosure = screen
      .getByTestId("dock-section-fire")
      .querySelector("button") as HTMLButtonElement;
    expect(disclosure.hasAttribute("aria-controls")).toBe(false);

    fireEvent.click(disclosure);

    const bodyId = disclosure.getAttribute("aria-controls") ?? "";
    expect(bodyId).not.toBe("");
    expect(document.getElementById(bodyId)).not.toBeNull();
    expect(document.getElementById(bodyId)).toBe(
      screen.getByTestId("dock-section-body-fire")
    );
  });

  it("gives every category its report and every layerless report its own section", () => {
    renderDock();
    openPanel();

    for (const label of [
      "Fire Dashboard",
      "Water Scarcity",
      "Vegetation & Land Cover",
      "Soil Health & Carbon",
      "Strategy Requests",
      "Environmental Alerts",
      "Team Dashboard",
      "Environmental Analytics",
    ]) {
      expect(detailsToggleFor(label), label).toBeTruthy();
    }
  });

  // The ungoverned bucket carries layers, not a report: there is no panel body behind it.
  it("gives the Basemap bucket no report to disclose", () => {
    renderDock();
    openPanel();

    const basemap = screen.getByTestId("layer-group-Basemap");
    expect(basemap.querySelector('[data-testid^="dock-section-"]')).toBeNull();
  });

  // JSX drops whitespace that spans a newline between two children, so the label and the
  // count ran together and this header announced as "Water0 of 5".
  it("speaks a group header's count as a separate word", () => {
    renderDock();
    openPanel();

    expect(screen.getByRole("button", { name: "Water 0 of 5" })).toBeTruthy();
  });

  it("expands one report without expanding any other", () => {
    renderDock();
    openPanel();

    fireEvent.click(detailsToggleFor("Water Scarcity"));

    expect(usePanelStore.getState().expandedDetails).toEqual(["water"]);
    expect(detailsToggleFor("Water Scarcity").getAttribute("aria-expanded")).toBe("true");
    expect(detailsToggleFor("Soil Health & Carbon").getAttribute("aria-expanded")).toBe(
      "false"
    );
  });

  // What the toolbar's alert bell does. The dock is closed at that moment, so this is also
  // what proves the shortcut does not merely expand a section nobody can see.
  it("opens the dock at the section a shortcut focuses", () => {
    renderDock();
    expect(screen.queryByTestId("layer-panel")).toBeNull();

    act(() => {
      usePanelStore.getState().focusDockSection("alerts");
    });

    expect(screen.getByTestId("layer-panel")).toBeTruthy();
    expect(detailsToggleFor("Environmental Alerts").getAttribute("aria-expanded")).toBe(
      "true"
    );
    // Consumed on arrival, so a later expansion by hand does not re-scroll the dock.
    expect(usePanelStore.getState().pendingScrollSection).toBeNull();
  });

  // A group's caret governs its rows and NOT its report: collapsing a category leaves an
  // index of reports behind rather than hiding them.
  it("keeps a category's report reachable while its rows are collapsed", () => {
    renderDock();
    openPanel();

    // The group's own caret is the first button in its header row.
    const waterGroup = screen.getByTestId("layer-group-water");
    fireEvent.click(waterGroup.querySelector("button")!);

    expect(screen.queryByTestId("layer-row-sensors")).toBeNull();
    expect(detailsToggleFor("Water Scarcity")).toBeTruthy();
  });
});
