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
import { SEARCH_INPUT_DOM_ID } from "@/components/map/layer-panel/SearchDockSection";
import { LAYER_REGISTRY, LAYER_TOGGLE_IDS } from "@/lib/map/layer-registry";
import { DEFAULT_SOIL_FIELD_DEPTHS } from "@/lib/environmental/soil-field";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import { useSoilStore } from "@/stores/soil-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import type { SliderCapabilities } from "@/types/time-slider";

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

/**
 * A row's OPACITY slider, addressed by name.
 *
 * A row can hold two range inputs since 2026-08-09 -- strength and day -- so "the range input
 * in this row" is no longer an identifying description, and a positional query would quietly
 * begin asserting about whichever control happened to render first.
 */
function opacitySliderIn(row: HTMLElement, layerLabel: string): HTMLInputElement | null {
  return row.querySelector<HTMLInputElement>(
    `input[type="range"][aria-label="${layerLabel} opacity"]`
  );
}

/**
 * Enough of a payload for a layer row to draw a time slider. A row renders no track without one
 * -- the server's date is the only source of "today" here, and `sliderDomain` returns null
 * without an axis -- so a test about a row's scrubber has to seed it.
 *
 * `watersheds` is a SNAPSHOT on purpose: it is the case a row must NOT get a slider for, and the
 * only way to assert that is to have a published capability that still defines no axis.
 */
const SLIDER_CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: "2026-08-04",
  futureAxisDays: 30,
  layers: [
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2026-06-17",
      latestObservedDate: "2026-07-28",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      layerName: "watersheds",
      temporalKind: "snapshot",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2013-01-18",
      latestObservedDate: "2013-01-18",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
  ],
};

/** The one scrolling element in the dock, found the same way the contract test finds it. */
function scrollerWithin(panel: HTMLElement): Element {
  const scrollers = Array.from(panel.querySelectorAll("*")).filter((element) =>
    SCROLLING_CLASS.test(classListOf(element))
  );
  expect(scrollers).toHaveLength(1);
  return scrollers[0];
}

beforeEach(() => {
  useMapStore.setState({ activeLayers: [] });
  useLayerStore.setState({ layerOpacity: {} });
  usePanelStore.setState({
    layerPanelOpen: false,
    // Reset to nothing rather than to the store's own seed: these cases are about the reports.
    expandedDetails: [],
    pendingScrollSection: null,
  });
  useSoilStore.setState({ fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
  // Sparse and empty: an absent entry means the layer follows its own `latestObservedDate`,
  // which is the default every row opens on.
  useTimeSliderStore.setState({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: SLIDER_CAPABILITIES,
    capabilitiesUnavailable: false,
  });
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
  //
  // `top-4`, not `top-20`: the 5rem gap existed solely to clear the floating search field that
  // overlaid this panel's header, and that field is a section inside it since 2026-08-09.
  it("takes its height from the container rather than the viewport", () => {
    expect(PANEL_SHELL).not.toMatch(/\bmax-h-\[.*vh/);
    expect(PANEL_SHELL).toContain("top-4");
    expect(PANEL_SHELL).not.toContain("top-20");
    expect(PANEL_SHELL).toContain("bottom-4");
  });

  /**
   * The scrollbar is unpainted; the scrolling is not. `scrollbar-hidden` (globals.css) sets
   * `scrollbar-width: none` and zeroes the WebKit pseudo-element, and touches nothing else --
   * so this asserts the pair, because the failure mode of "hide the scrollbar" is reaching for
   * `overflow-hidden` and taking the scrolling with it.
   */
  it("hides the scrollbar without giving up the scrolling", () => {
    expect(PANEL_SCROLLER).toContain("scrollbar-hidden");
    expect(PANEL_SCROLLER).toContain("overflow-y-auto");
    expect(PANEL_SCROLLER).not.toContain("overflow-hidden");
  });

  /**
   * Mobile is the same tree in a different box: a full-screen overlay, because a 19rem drawer
   * on a 360px screen leaves a 56px strip that is neither a usable map nor a usable panel. The
   * z-lift is part of it -- the date pill and the hover tooltip are both z-10 and would
   * otherwise paint through an overlay that covers the whole viewport.
   */
  it("becomes a full-screen overlay on a phone", () => {
    expect(PANEL_SHELL).toContain("max-sm:inset-0");
    expect(PANEL_SHELL).toContain("max-sm:w-full");
    expect(PANEL_SHELL).toContain("max-sm:z-30");
    // Square-cornered and borderless: a rounded edge against the viewport edge reads as a
    // rendering fault rather than as a panel.
    expect(PANEL_SHELL).toContain("max-sm:rounded-none");
    expect(PANEL_SHELL).toContain("max-sm:border-0");
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

  // `building-footprints` belongs to no category, so
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

    // Selected by its own name rather than by "the range input in this row": a row can carry a
    // second range control now -- its time slider -- so a positional query would silently start
    // asserting about the wrong one.
    const slider = opacitySliderIn(rowFor("sensors"), "Sensor Stations");
    expect(slider).not.toBeNull();
    // Named and spoken: a bare range announces as an unnamed slider whose value reads "0.6".
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

    const slider = opacitySliderIn(rowFor("sensors"), "Sensor Stations");
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

  /**
   * The header carried a second legend eye until 2026-08-09, over a corner card the reader
   * could not see while using the control that hid it. The legend is the collapsed state of
   * this panel now, so there is one control left in this header and it is "put it away".
   */
  it("leaves one control in its header, and it is the close button", () => {
    renderDock();
    const panel = openPanel();

    const header = panel.querySelector("header") as HTMLElement;
    const headerButtons = Array.from(header.querySelectorAll("button"));
    expect(headerButtons).toHaveLength(1);
    expect(headerButtons[0].getAttribute("aria-label")).toBe("Close map manager");
    expect(screen.queryByRole("button", { name: "Hide legend entries" })).toBeNull();
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

/**
 * The time scrubber was a dock SECTION for one day: it joined on 2026-08-08 as one card above
 * every category, and on 2026-08-09 it became one slider per layer row. These cases replace the
 * "LayerPanel time section" describe wholesale, because the surface they pinned -- a single
 * `dock-section-time` holding a single `time-slider` card, seeded open, addressable by
 * `focusDockSection("time")` -- is genuinely gone rather than moved.
 *
 * What survives is the claim underneath it: the map's day must be reachable and legible. It is
 * now reachable on the row that owns it, which is what these assert instead.
 */
describe("LayerPanel per-layer time sliders", () => {
  it("has no map-wide time section, card or caret left anywhere in the dock", () => {
    renderDock();
    openPanel();

    expect(screen.queryByTestId("dock-section-time")).toBeNull();
    expect(screen.queryByTestId("time-slider")).toBeNull();
    // The card's own title. A section named "Map date" would be a control over a date the map
    // no longer has.
    expect(screen.queryByRole("button", { name: "Map date" })).toBeNull();
  });

  it("gives a temporal layer's row its own slider, only once that layer is switched on", () => {
    renderDock();
    openPanel();

    // Off: no control at all, the same rule the opacity slider follows -- a control that
    // adjusts nothing is a fabricated affordance.
    expect(rowFor("water").querySelector('[data-testid="layer-time-slider-slot-water"]')).toBeNull();

    act(() => {
      useMapStore.getState().toggleLayer("water");
    });

    expect(screen.getByTestId("layer-time-slider-slot-water")).toBeTruthy();
  });

  /**
   * The gate that keeps a dead control off a row. `watersheds` publishes a real capability with
   * a real date -- 96% of its 9,396 HUC12 basins carry one 2013 WBD loaddate -- but it is a
   * SNAPSHOT and defines no axis, so a track over it would advertise years of scrubbing across a
   * boundary set that draws identically on every one of those days.
   */
  it("gives a snapshot layer no slider even though it carries a published date", () => {
    renderDock();
    openPanel();

    act(() => {
      useMapStore.getState().toggleLayer("watersheds");
    });

    expect(screen.getByTestId("layer-row-watersheds")).toBeTruthy();
    expect(screen.queryByTestId("layer-time-slider-slot-watersheds")).toBeNull();
  });

  // `soil` has no `geo.layers` row behind it at all, so there is no axis to draw and no day to
  // scrub -- a slider there would be a control over nothing.
  it("gives a layer with no warehouse feed behind it no slider", () => {
    renderDock();
    openPanel();

    act(() => {
      useMapStore.getState().toggleLayer("soil");
    });

    expect(screen.getByTestId("layer-row-soil")).toBeTruthy();
    expect(screen.queryByTestId("layer-time-slider-slot-soil")).toBeNull();
  });

  /**
   * The row's slider is the ONE place a day is stated, so this checks it reaches the row rather
   * than re-checking what it says -- `LayerTimeSlider.test.tsx` owns the track, the date field
   * and the behind-latest mark. Two surfaces stating one day, one line apart, is the defect the
   * dock already recorded once when `TimeSlider` printed "Map date" directly under a section
   * header saying "Map date".
   */
  it("states a scrubbed layer's day exactly once, from the row's own slider", () => {
    renderDock();
    openPanel();

    act(() => {
      useMapStore.getState().toggleLayer("water");
    });
    act(() => {
      useTimeSliderStore.getState().setLayerDate("water", "2026-07-02");
    });

    const row = rowFor("water");
    // Exactly one date field in the row, and it is inside the slider slot. Two would be two
    // controls over one value -- the drift the dock was built to end -- and the count is the
    // assertion, not an incidental detail.
    const dateFields = row.querySelectorAll<HTMLInputElement>('input[type="date"]');
    expect(dateFields).toHaveLength(1);
    expect(screen.getByTestId("layer-time-slider-slot-water").contains(dateFields[0])).toBe(true);
    expect(dateFields[0].value).toBe("2026-07-02");
  });

  it("keeps the scroll contract with a row's slider mounted", () => {
    renderDock();
    const panel = openPanel();
    act(() => {
      useMapStore.getState().toggleLayer("water");
    });

    // A track inside a 19rem column is exactly where a stray `overflow-*` would produce the
    // second-scrollbar defect panel-scroll.ts rule 2 names -- and it is the row's only drag
    // control, so a nested scroller is what makes it unreachable on a phone.
    expect(screen.getByTestId("layer-time-slider-slot-water")).toBeTruthy();
    expect(scrollerWithin(panel)).toBeTruthy();
  });
});

/**
 * The floating search field and the bottom toolbar became sections on 2026-08-09. Both had been
 * competing surfaces: the field overlapped this panel's own header, and the toolbar's fifth
 * button wrote the same `activeLayers` entry the layer tree's Basemap row writes.
 *
 * These cases pin the two properties that make the merge a fix rather than a move -- the
 * sections lead the scroller because each governs every layer, and the View section owns no
 * layer switch at all.
 */
describe("LayerPanel control sections", () => {
  /**
   * Rewritten from "leads the scroller with where, when and how": "when" is no longer among
   * them. It was a third control section for one day, and it governed every layer -- which is
   * exactly why it could not survive per-layer dates, since the day is now a property of one
   * row rather than of the map. What is left still governs the whole map and still leads.
   */
  it("leads the scroller with where and how, above every category", () => {
    renderDock();
    const panel = openPanel();

    const search = screen.getByTestId("dock-section-search");
    const view = screen.getByTestId("dock-section-view");
    const firstGroup = screen.getByTestId("layer-group-fire");

    for (const section of [search, view]) {
      expect(scrollerWithin(panel).contains(section)).toBe(true);
    }
    // A control that governs every layer, filed among the categories, would read as belonging
    // to whichever one it landed beside.
    expect(search.compareDocumentPosition(view) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      view.compareDocumentPosition(firstGroup) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it("puts the search field in the panel, with the Ctrl+K shortcut's handle on it", () => {
    renderDock();
    openPanel();
    fireEvent.click(detailsToggleFor("Search places"));

    const field = screen.getByRole("combobox", { name: "Search places" });
    // The shortcut cannot hold a ref into a section that is unmounted while collapsed, so the
    // handle it focuses is a DOM id -- see MapKeyboardShortcuts.
    expect(field.id).toBe(SEARCH_INPUT_DOM_ID);
  });

  // Photon answers names, not numbers, so a typed coordinate pair is the one thing the deleted
  // command palette could do that geocoding cannot. It is bounds-checked because MapLibre
  // throws on a non-finite centre rather than ignoring it.
  it("offers a coordinate jump for a typed lat/lon pair, and only for a valid one", () => {
    renderDock();
    openPanel();
    fireEvent.click(detailsToggleFor("Search places"));
    const field = screen.getByRole("combobox", { name: "Search places" });

    fireEvent.change(field, { target: { value: "45.52, -122.68" } });
    expect(screen.getByText(/Go to 45\.5200, -122\.6800/)).toBeTruthy();

    fireEvent.change(field, { target: { value: "145.52, -122.68" } });
    expect(screen.queryByText(/^Go to /)).toBeNull();
  });

  /**
   * The whole reason render mode is its own section. Changing how the map is lit must never add
   * or drop a layer, and the toolbar this replaced broke that with a building-footprints button
   * sitting between the globe and the 3D toggles.
   */
  it("gives render mode no layer switch of its own", () => {
    renderDock();
    openPanel();
    fireEvent.click(detailsToggleFor("Map view"));

    const view = screen.getByTestId("dock-section-body-view");
    const switchNames = Array.from(view.querySelectorAll('[role="switch"]')).map((control) =>
      control.textContent
    );
    expect(switchNames).toEqual(["Terrain", "Globe", "3D tilt"]);
    expect(view.textContent).not.toContain(LAYER_REGISTRY["building-footprints"].label);
  });

  it("keeps the layer no category governs switchable from its Basemap row alone", () => {
    renderDock();
    openPanel();

    // Withheld, so the eye is disabled -- but it exists, and it is the only one. The toolbar
    // button that used to be the second is gone.
    const eyes = screen.getAllByRole("switch", { name: "Show 3D Building Footprints on map" });
    expect(eyes).toHaveLength(1);
  });

  it("changes render mode without touching what is drawn", () => {
    renderDock();
    openPanel();
    act(() => {
      useMapStore.getState().toggleLayer("sensors");
    });
    fireEvent.click(detailsToggleFor("Map view"));

    fireEvent.click(screen.getByRole("switch", { name: "Terrain" }));
    fireEvent.click(screen.getByRole("switch", { name: "Globe" }));
    fireEvent.click(screen.getByRole("switch", { name: "3D tilt" }));

    expect(useMapStore.getState().isTerrainEnabled).toBe(true);
    expect(useMapStore.getState().isGlobeView).toBe(true);
    expect(useMapStore.getState().is3DEnabled).toBe(true);
    expect(useMapStore.getState().activeLayers).toEqual(["sensors"]);
  });
});
