import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import HoverTooltip from "@/components/map/HoverTooltip";
import { setScalarFieldInspectionSuppressed } from "@/lib/map/scalar-field-inspection";

/**
 * Touch fires no `mousemove` at all, so every `TOOLTIP_TAP_LAYER_IDS` layer was uninspectable on
 * a phone before the tap handler this file covers -- a tap on one of them did nothing whatsoever,
 * not even the empty-ground "ask AI about this location" prompt, because `MapView`'s own click
 * handler already swallows any click that landed on a rendered feature. These tests hold the
 * three properties that handler owes: it is a no-op on a fine pointer (hover already works
 * there), it widens the hit test for a coarse one, and a pinned tooltip is dismissed by a second
 * tap rather than requiring a hover-out that touch cannot produce.
 */

interface FakeFeature {
  layer: { id: string };
  properties: Record<string, unknown>;
}

function createFakeMap(initialFeatures: FakeFeature[]) {
  const listeners = new Map<string, Set<(event: unknown) => void>>();
  const canvas = { style: { cursor: "" } };
  // A `let`, not a closed-over constant: "tap empty ground" is simulated by clearing this
  // between two emits on the SAME map/component, exactly as a real second tap landing off every
  // feature would query and find nothing.
  let features = initialFeatures;
  const queryRenderedFeatures = vi.fn((_geometry: unknown, options: { layers: string[] }) =>
    features.filter((feature) => options.layers.includes(feature.layer.id))
  );

  return {
    on(type: string, handler: (event: unknown) => void) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
    },
    off(type: string, handler: (event: unknown) => void) {
      listeners.get(type)?.delete(handler);
    },
    emit(type: string, event: unknown) {
      for (const handler of Array.from(listeners.get(type) ?? [])) handler(event);
    },
    setFeatures(next: FakeFeature[]) {
      features = next;
    },
    getStyle: () => ({ layers: [] }),
    getLayer: () => ({}),
    queryRenderedFeatures,
    getCanvas: () => canvas,
    getContainer: () => ({ clientWidth: 800, clientHeight: 600 }),
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

function asMap(fakeMap: FakeMap): MapLibreMap {
  return fakeMap as unknown as MapLibreMap;
}

function setCoarsePointer(isCoarse: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: isCoarse }) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  vi.restoreAllMocks();
  // @ts-expect-error -- jsdom implements no matchMedia by default; undo the per-test stub.
  delete window.matchMedia;
});

describe("HoverTooltip: mousemove (fine pointer, unchanged)", () => {
  it("shows a tooltip for a hovered feature", () => {
    setCoarsePointer(false);
    const fakeMap = createFakeMap([
      // `network` is the only field `formatSensorStation` renders from this fixture's fields;
      // an all-empty bag makes `buildContent` return null and no tooltip would ever appear,
      // for any scenario in this file, including this "unchanged" baseline.
      { layer: { id: "sensors" }, properties: { network: "TEST" } },
    ]);
    const { getByText } = render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("mousemove", { point: { x: 100, y: 120 } });
    });

    expect(getByText("Weather station")).toBeTruthy();
    // The event's own point, never a padded box: a mouse cursor needs no tolerance.
    expect(fakeMap.queryRenderedFeatures).toHaveBeenLastCalledWith(
      { x: 100, y: 120 },
      expect.objectContaining({ layers: expect.arrayContaining(["sensors"]) })
    );
  });
});

describe("HoverTooltip: click (coarse pointer only)", () => {
  it("does nothing on a fine pointer -- hover already covers a mouse", () => {
    setCoarsePointer(false);
    const fakeMap = createFakeMap([{ layer: { id: "sensors" }, properties: {} }]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });

    expect(queryByText("Weather station")).toBeNull();
  });

  it("pins a tooltip open for a layer with no dedicated click popup", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([
      { layer: { id: "sensors" }, properties: { network: "TEST" } },
    ]);
    const { getByText, getByLabelText } = render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });

    expect(getByText("Weather station")).toBeTruthy();
    // Pinned tooltips carry a close affordance a hover-only one never needs.
    expect(getByLabelText("Close")).toBeTruthy();
  });

  it("widens the hit test to a padded box on a coarse pointer", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([{ layer: { id: "sensors" }, properties: {} }]);
    render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });

    expect(fakeMap.queryRenderedFeatures).toHaveBeenLastCalledWith(
      [
        [88, 108],
        [112, 132],
      ],
      expect.objectContaining({ layers: expect.arrayContaining(["sensors"]) })
    );
  });

  it("never reaches published-fire-circles or the other five ids FireLayer/WaterLayer already own", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([
      { layer: { id: "published-fire-circles" }, properties: {} },
    ]);
    render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });

    const [, options] = fakeMap.queryRenderedFeatures.mock.calls.at(-1)!;
    expect((options as { layers: string[] }).layers).not.toContain("published-fire-circles");
  });

  it("dismisses a pinned tooltip on a second tap of the same feature", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([
      { layer: { id: "sensors" }, properties: { network: "TEST" } },
    ]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });
    expect(queryByText("Weather station")).toBeTruthy();

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });
    expect(queryByText("Weather station")).toBeNull();
  });

  it("dismisses a pinned tooltip on a tap that lands on empty ground", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([
      { layer: { id: "sensors" }, properties: { network: "TEST" } },
    ]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });
    expect(queryByText("Weather station")).toBeTruthy();

    // The second tap lands where nothing is rendered.
    fakeMap.setFeatures([]);
    act(() => {
      fakeMap.emit("click", { point: { x: 500, y: 500 } });
    });
    expect(queryByText("Weather station")).toBeNull();
  });

  it("dismisses a pinned tooltip through its own close button", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([
      { layer: { id: "sensors" }, properties: { network: "TEST" } },
    ]);
    const { getByLabelText, queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);

    act(() => {
      fakeMap.emit("click", { point: { x: 100, y: 120 } });
    });
    fireEvent.click(getByLabelText("Close"));

    expect(queryByText("Weather station")).toBeNull();
  });
});

describe("HoverTooltip: measured vegetation", () => {
  it.each([{ x: 195, y: 422 }, { x: 380, y: 830 }])("keeps a measured caption inside a phone viewport at %o", point => {
    setCoarsePointer(true);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(240);
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(180);
    const fakeMap = createFakeMap([{ layer: { id: "vegetation-ndvi-cells-fill" }, properties: { ndvi: -0.4 } }]);
    vi.spyOn(fakeMap, "getContainer").mockReturnValue({ clientWidth: 390, clientHeight: 844 });
    const { getByText } = render(<HoverTooltip map={asMap(fakeMap)} />);
    act(() => fakeMap.emit("click", { point }));
    const element = getByText("Measured vegetation cell").parentElement!;
    const left = Number.parseFloat(element.style.left);
    const top = Number.parseFloat(element.style.top);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + element.offsetWidth).toBeLessThanOrEqual(390);
    expect(top).toBeGreaterThanOrEqual(0);
    expect(top + element.offsetHeight).toBeLessThanOrEqual(844);
  });

  it.each([false, true])("inspects native cells with coarse pointer %s and clears on style replacement", coarse => {
    setCoarsePointer(coarse);
    const fakeMap = createFakeMap([{ layer: { id: "vegetation-ndvi-cells-fill" }, properties: {
      ndvi: -0.25, observedDay: "2026-09-01", cellId: "negative-water-cell",
    } }]);
    const { getByText, queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);
    act(() => fakeMap.emit(coarse ? "click" : "mousemove", { point: { x: 100, y: 120 } }));
    expect(getByText("NDVI: -0.25 (dimensionless)")).toBeTruthy();
    act(() => fakeMap.emit("style.load", {}));
    expect(queryByText("Measured vegetation cell")).toBeNull();
  });
});

describe("HoverTooltip: vegetation source invalidation", () => {
  it.each([false, true])("clears and blocks refused cells before native source events with coarse pointer %s", coarse => {
    setCoarsePointer(coarse);
    const fakeMap = createFakeMap([{ layer: { id: "vegetation-ndvi-cells-fill" }, properties: { ndvi: 0.4 } }]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);
    const inspect = () => fakeMap.emit(coarse ? "click" : "mousemove", { point: { x: 100, y: 120 } });
    act(inspect);
    expect(queryByText("Measured vegetation cell")).toBeTruthy();
    act(() => setScalarFieldInspectionSuppressed(fakeMap, ["vegetation-ndvi-cells-fill"], true));
    expect(queryByText("Measured vegetation cell")).toBeNull();
    expect(fakeMap.getCanvas().style.cursor).toBe("");
    act(inspect);
    expect(queryByText("Measured vegetation cell")).toBeNull();
    expect(fakeMap.queryRenderedFeatures.mock.calls.at(-1)?.[1].layers).not.toContain("vegetation-ndvi-cells-fill");
    fakeMap.setFeatures([{ layer: { id: "vegetation-ndvi-cells-fill" }, properties: { ndvi: -0.4 } }]);
    act(() => setScalarFieldInspectionSuppressed(fakeMap, ["vegetation-ndvi-cells-fill"], false));
    act(inspect);
    expect(queryByText("NDVI: -0.4 (dimensionless)")).toBeTruthy();
  });

  it("keeps another layer's pinned caption when scalar inspection is invalidated", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([{ layer: { id: "sensors" }, properties: { network: "TEST" } }]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);
    act(() => fakeMap.emit("click", { point: { x: 100, y: 120 } }));
    act(() => setScalarFieldInspectionSuppressed(fakeMap, ["vegetation-ndvi-cells-fill"], true));
    expect(queryByText("Weather station")).toBeTruthy();
  });

  it("keeps the drawn-day caption while loading then clears it when replacement content lands", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([{ layer: { id: "vegetation-ndvi-cells-fill" }, properties: { ndvi: 0.4 } }]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);
    act(() => fakeMap.emit("click", { point: { x: 100, y: 120 } }));
    act(() => fakeMap.emit("sourcedata", { sourceId: "vegetation-ndvi-cells", sourceDataType: "metadata" }));
    expect(queryByText("Measured vegetation cell")).toBeTruthy();
    act(() => fakeMap.emit("sourcedata", { sourceId: "unrelated", sourceDataType: "content" }));
    expect(queryByText("Measured vegetation cell")).toBeTruthy();
    act(() => fakeMap.emit("sourcedata", { sourceId: "vegetation-ndvi-cells", sourceDataType: "content" }));
    expect(queryByText("Measured vegetation cell")).toBeNull();
  });

  it("does not dismiss another layer's pinned caption", () => {
    setCoarsePointer(true);
    const fakeMap = createFakeMap([{ layer: { id: "sensors" }, properties: { network: "TEST" } }]);
    const { queryByText } = render(<HoverTooltip map={asMap(fakeMap)} />);
    act(() => fakeMap.emit("click", { point: { x: 100, y: 120 } }));
    act(() => fakeMap.emit("sourcedata", { sourceId: "vegetation-ndvi-cells", sourceDataType: "content" }));
    expect(queryByText("Weather station")).toBeTruthy();
  });
});
