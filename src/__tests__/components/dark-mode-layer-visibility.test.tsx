import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { FireLayer } from "@/components/map/layers/FireLayer";
import { WaterLayer } from "@/components/map/layers/WaterLayer";

/**
 * Reproduces the reported bug: a hard load in dark mode leaves fire/water
 * invisible because `map.isStyleLoaded()` reads false at mount and the old
 * code's `map.once("style.load", addAllLayers)` was registered against an
 * event that had already fired -- it never runs again. See
 * src/components/map/AGENTS.md "Custom-added layers need a retriggerable
 * readiness signal, not once()".
 *
 * This fake models exactly the parts of maplibre-gl these two components
 * touch: an event emitter driven independently of isStyleLoaded() (so a
 * test can reproduce "style.load already fired, isStyleLoaded() still
 * false" without those being coupled, same as LayerManager.test.tsx's fake),
 * plus a real add/getLayer/getSource bookkeeping map so idempotency
 * (calling addAllLayers twice) throws exactly like the real maplibre-gl
 * would on a duplicate id.
 */
function createFakeMap() {
  const listeners = new Map<string, Set<(...args: unknown[]) => void>>();
  let styleLoaded = false;
  const sources = new Map<string, { data: unknown; setData: ReturnType<typeof vi.fn> }>();
  const layers = new Set<string>();

  function on(type: string, a: unknown, b?: unknown) {
    const handler = (typeof b === "function" ? b : a) as (...args: unknown[]) => void;
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type)!.add(handler);
  }
  function off(type: string, a: unknown, b?: unknown) {
    const handler = (typeof b === "function" ? b : a) as (...args: unknown[]) => void;
    listeners.get(type)?.delete(handler);
  }

  return {
    on,
    off,
    isStyleLoaded: () => styleLoaded,
    setStyleLoaded(value: boolean) {
      styleLoaded = value;
    },
    emit(type: string) {
      for (const handler of Array.from(listeners.get(type) ?? [])) handler();
    },
    getStyle: () => ({ layers: [] }),
    addSource: (id: string, opts: { data: unknown }) => {
      if (sources.has(id)) throw new Error(`Source "${id}" already exists.`);
      sources.set(id, { data: opts.data, setData: vi.fn() });
    },
    getSource: (id: string) => sources.get(id),
    removeSource: (id: string) => {
      sources.delete(id);
    },
    addLayer: (layerObj: { id: string }) => {
      if (layers.has(layerObj.id)) {
        throw new Error(`Layer "${layerObj.id}" already exists on this map.`);
      }
      layers.add(layerObj.id);
    },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    removeLayer: (id: string) => {
      layers.delete(id);
    },
    setPaintProperty: vi.fn(),
    /** Simulates a basemap swap wiping every custom-added source/layer. */
    wipeCustomLayersAndSources() {
      layers.clear();
      sources.clear();
    },
    hasSource: (id: string) => sources.has(id),
    hasLayer: (id: string) => layers.has(id),
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

const FIRE_GEOJSON: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      geometry: { type: "Point", coordinates: [-116.2, 43.6] },
      properties: { brightness: 400, confidence: 80 },
    },
  ],
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("FireLayer dark-mode hard-load race", () => {
  it("adds nothing while isStyleLoaded() reads false and no style.load fires again", () => {
    const fakeMap = createFakeMap(); // styleLoaded stays false, style.load never re-fires
    render(<FireLayer map={fakeMap as unknown as MapLibreMap} visible geojson={FIRE_GEOJSON} />);

    expect(fakeMap.hasSource("published-fire-source")).toBe(false);
    expect(fakeMap.hasLayer("published-fire-circles")).toBe(false);
  });

  it("adds the layers once styledata catches the style up, with no further style.load", () => {
    const fakeMap = createFakeMap();
    render(<FireLayer map={fakeMap as unknown as MapLibreMap} visible geojson={FIRE_GEOJSON} />);

    expect(fakeMap.hasLayer("published-fire-circles")).toBe(false);

    // Tiles finish loading; styledata fires (repeatedly, in the real map) but
    // style.load does not fire again for this style.
    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });

    expect(fakeMap.hasSource("published-fire-source")).toBe(true);
    expect(fakeMap.hasLayer("published-fire-circles")).toBe(true);
    expect(fakeMap.hasLayer("published-fire-outlines")).toBe(true);
  });

  it("re-adds layers across a basemap swap without throwing on duplicates", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    render(<FireLayer map={fakeMap as unknown as MapLibreMap} visible geojson={FIRE_GEOJSON} />);
    expect(fakeMap.hasLayer("published-fire-circles")).toBe(true);

    // A basemap swap: setState()'s diff path wipes custom layers/sources and
    // fires style.load synchronously; tiles for the new style have not
    // loaded yet, so isStyleLoaded() reads false again.
    act(() => {
      fakeMap.wipeCustomLayersAndSources();
      fakeMap.setStyleLoaded(false);
      fakeMap.emit("style.load");
    });
    // The persistent style.load listener re-adds immediately -- addLayer/
    // addSource only require the style to be loaded, not its tiles.
    expect(fakeMap.hasLayer("published-fire-circles")).toBe(true);

    // Sources land and styledata fires too; must not throw on the
    // already-present source/layer (idempotency guard).
    expect(() => {
      act(() => {
        fakeMap.setStyleLoaded(true);
        fakeMap.emit("styledata");
      });
    }).not.toThrow();
    expect(fakeMap.hasLayer("published-fire-circles")).toBe(true);
  });
});

describe("WaterLayer dark-mode hard-load race", () => {
  const gauges = [
    {
      siteNo: "1",
      siteName: "Boise River",
      lon: -116.2,
      lat: 43.6,
      flowCfs: 120,
      percentile: 40,
      condition: "normal" as const,
      trend: "stable" as const,
      updatedAt: "2026-08-01T00:00:00Z",
    },
  ];

  function renderWaterLayer(fakeMap: FakeMap) {
    return render(
      <WaterLayer map={fakeMap as unknown as MapLibreMap} visible gauges={gauges} wells={[]} />
    );
  }

  it("adds nothing while isStyleLoaded() reads false and no style.load fires again", () => {
    const fakeMap = createFakeMap();
    renderWaterLayer(fakeMap);

    expect(fakeMap.hasSource("water-gauges")).toBe(false);
    expect(fakeMap.hasLayer("water-gauges-circle")).toBe(false);
  });

  it("adds the layers once styledata catches the style up", () => {
    const fakeMap = createFakeMap();
    renderWaterLayer(fakeMap);
    expect(fakeMap.hasLayer("water-gauges-circle")).toBe(false);

    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });

    expect(fakeMap.hasSource("water-gauges")).toBe(true);
    expect(fakeMap.hasLayer("water-gauges-circle")).toBe(true);
  });

  it("re-adds layers across a basemap swap without throwing on duplicates", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderWaterLayer(fakeMap);
    expect(fakeMap.hasLayer("water-gauges-circle")).toBe(true);

    act(() => {
      fakeMap.wipeCustomLayersAndSources();
      fakeMap.setStyleLoaded(false);
      fakeMap.emit("style.load");
    });
    expect(fakeMap.hasLayer("water-gauges-circle")).toBe(true);

    expect(() => {
      act(() => {
        fakeMap.setStyleLoaded(true);
        fakeMap.emit("styledata");
      });
    }).not.toThrow();
    expect(fakeMap.hasLayer("water-gauges-circle")).toBe(true);
  });
});
