import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CustomLayerInterface,
  LayerSpecification,
  Map as MapLibreMap,
  SymbolLayerSpecification,
  VisibilitySpecification,
} from "maplibre-gl";
import { VegetationLayer } from "@/components/map/layers/VegetationLayer";
import { getNDVITileUrl } from "@/lib/vegetation";
import { useVegetationStore } from "@/stores/vegetation-store";

/**
 * Parsed-style admission, modelled the way `dark-mode-layer-visibility.test.tsx` models it for
 * Fire and Water: style PARSING (`getStyle()` returns an object, so addSource/addLayer are
 * legal) is a strictly earlier and independent event from SOURCE readiness
 * (`isStyleLoaded() === true`, which waits on every unrelated source in the basemap).
 *
 * Tile completion is emitted as `sourcedata` only. Fabricating a `styledata`/`style.load` event
 * to stand in for it would hide exactly the bug this file exists for: a component that mounts
 * after `style.load` has already fired never receives another one.
 */
const CELL_SOURCE_ID = "vegetation-ndvi-cells";
const CELL_FILL_ID = "vegetation-ndvi-cells-fill";
const CELL_OUTLINE_ID = "vegetation-ndvi-cells-outline";
const CELL_LABEL_ID = "vegetation-ndvi-cells-values";
const FIELD_ID = "vegetation-ndvi-scalar-field";
const RASTER_ID = "ndvi-overlay-layer";
const RASTER_SOURCE_ID = "ndvi-overlay";

/** A composite period GIBS actually publishes, so the raster half of the layer is exercised. */
const RASTER_YEAR = 2025;
const RASTER_MONTH = 6;
const RASTER_AVAILABLE = getNDVITileUrl(RASTER_YEAR, RASTER_MONTH) !== "";

type Listener = (event?: unknown) => void;
type TestLayer = LayerSpecification | CustomLayerInterface;
type TestSource = {
  type: string;
  data: GeoJSON.FeatureCollection | null;
  tiles: string[];
  setData: ReturnType<typeof vi.fn>;
  setTiles: ReturnType<typeof vi.fn>;
};

function cells(revision: number): GeoJSON.FeatureCollection {
  if (revision === 0) return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [[[-116.25, 43.5], [-116.0, 43.5], [-116.0, 43.75], [-116.25, 43.75], [-116.25, 43.5]]],
        },
        properties: { ndvi: 0.1 * revision, observedDay: "2026-08-28" },
      },
    ],
  };
}

function createFakeMap(parsed = true) {
  let styleParsed = parsed;
  let sourcesLoaded = false;
  const sources = new Map<string, TestSource>();
  const layers = new Map<string, TestLayer>();
  const layout = new Map<string, VisibilitySpecification | undefined>();
  const paint = new Map<string, Record<string, unknown>>();
  const listeners = new Map<string, Set<Listener>>();
  const requireParsed = () => {
    // Real maplibre-gl refuses both calls before the style is parsed; a regression that drops
    // the getStyle() gate has to fail loudly here rather than quietly install nothing.
    if (!styleParsed) throw new Error("Style is not done loading.");
  };
  const emit = (type: string, event?: unknown) => {
    for (const listener of [...(listeners.get(type) ?? [])]) listener(event);
  };
  const map = {
    on: vi.fn((type: string, listener: Listener) => {
      const current = listeners.get(type) ?? new Set<Listener>();
      current.add(listener);
      listeners.set(type, current);
    }),
    off: vi.fn((type: string, listener: Listener) => {
      listeners.get(type)?.delete(listener);
    }),
    getStyle: () => (styleParsed ? { version: 8, sources: {}, layers: [...layers.values()] } : undefined),
    isStyleLoaded: () => styleParsed && sourcesLoaded,
    isSourceLoaded: () => sourcesLoaded,
    getSource: (id: string) => sources.get(id),
    getLayer: (id: string) => layers.get(id),
    addSource: vi.fn((id: string, input: { type: string; data?: GeoJSON.FeatureCollection; tiles?: string[] }) => {
      requireParsed();
      if (sources.has(id)) throw new Error(`Source "${id}" already exists.`);
      const source: TestSource = {
        type: input.type,
        data: input.data ?? null,
        tiles: input.tiles ?? [],
        setData: vi.fn(),
        setTiles: vi.fn(),
      };
      source.setData.mockImplementation((data: GeoJSON.FeatureCollection) => { source.data = data; });
      source.setTiles.mockImplementation((tiles: string[]) => { source.tiles = tiles; });
      sources.set(id, source);
    }),
    addLayer: vi.fn((layer: TestLayer) => {
      requireParsed();
      if (layers.has(layer.id)) throw new Error(`Layer "${layer.id}" already exists on this map.`);
      layers.set(layer.id, layer);
      layout.set(layer.id, "layout" in layer ? layer.layout?.visibility : undefined);
      paint.set(layer.id, { ...("paint" in layer ? layer.paint : undefined) });
    }),
    removeLayer: vi.fn((id: string) => { layers.delete(id); layout.delete(id); paint.delete(id); }),
    removeSource: vi.fn((id: string) => { sources.delete(id); }),
    getLayoutProperty: (id: string) => layout.get(id),
    setLayoutProperty: vi.fn((id: string, _property: string, value: VisibilitySpecification) => {
      if (!layers.has(id)) throw new Error(`Missing layer ${id}`);
      layout.set(id, value);
    }),
    setPaintProperty: vi.fn((id: string, property: string, value: unknown) => {
      if (!layers.has(id)) throw new Error(`Missing layer ${id}`);
      paint.set(id, { ...paint.get(id), [property]: value });
    }),
    triggerRepaint: vi.fn(),
  };
  return {
    map: map as unknown as MapLibreMap,
    calls: map,
    sources,
    layers,
    layout,
    paint,
    listeners,
    emit,
    /** Tile completion. NOT a style event -- see the file header. */
    completeSources: () => { sourcesLoaded = true; emit("sourcedata"); },
    parseStyle: () => { styleParsed = true; sourcesLoaded = false; emit("style.load"); },
    swapStyle: () => { sources.clear(); layers.clear(); layout.clear(); paint.clear(); styleParsed = true; sourcesLoaded = false; emit("style.load"); },
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

/** Everything the native (non-scalar) rendering path owns for a requestable composite period. */
const EXPECTED_SOURCES = [CELL_SOURCE_ID, ...(RASTER_AVAILABLE ? [RASTER_SOURCE_ID] : [])];
const EXPECTED_LAYERS = [CELL_FILL_ID, CELL_OUTLINE_ID, ...(RASTER_AVAILABLE ? [RASTER_ID] : [])];

function element(map: MapLibreMap, revision: number, visible = true, period = true) {
  return (
    <VegetationLayer
      map={map}
      visible={visible}
      geojson={cells(revision)}
      year={period ? RASTER_YEAR : null}
      month={period ? RASTER_MONTH : null}
      opacityScale={revision === 2 ? 0.5 : 1}
    />
  );
}

function assertInstalled(fixture: FakeMap, revision: number) {
  expect([...fixture.sources.keys()].sort()).toEqual([...EXPECTED_SOURCES].sort());
  expect([...fixture.layers.keys()].sort()).toEqual([...EXPECTED_LAYERS].sort());
  expect(fixture.sources.get(CELL_SOURCE_ID)?.data).toEqual(cells(revision));
  const opacity = revision === 2 ? 0.75 * 0.5 : 0.75;
  expect(fixture.paint.get(CELL_FILL_ID)?.["fill-opacity"]).toBe(opacity);
  if (RASTER_AVAILABLE) expect(fixture.paint.get(RASTER_ID)?.["raster-opacity"]).toBe(opacity);
}

/**
 * The exclusivity rule, asserted against the map rather than against the component: the GIBS
 * composite and the measured cells are ALTERNATIVE views of NDVI, so at most one of them is
 * ever laid out visible, whichever path admitted them.
 */
function assertExclusive(fixture: FakeMap, expected: "measured" | "satellite") {
  const rasterVisible = fixture.layout.get(RASTER_ID) === "visible";
  const cellsVisible = fixture.layout.get(CELL_FILL_ID) === "visible";
  expect(rasterVisible && cellsVisible).toBe(false);
  expect(cellsVisible).toBe(expected === "measured");
  if (RASTER_AVAILABLE) expect(rasterVisible).toBe(expected === "satellite");
  expect(fixture.layout.get(CELL_OUTLINE_ID)).toBe(fixture.layout.get(CELL_FILL_ID));
}

afterEach(() => {
  vi.unstubAllEnvs();
  act(() => useVegetationStore.setState({ source: "measured" }));
});

describe("VegetationLayer parsed-style admission", () => {
  // Admission is asserted against the native rendering path; the scalar controller has its own
  // describe below, and leaving the flag ambient would make EXPECTED_LAYERS environment-dependent.
  beforeEach(() => vi.stubEnv("NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS", ""));

  it("installs on a delayed mount while isStyleLoaded() is still false", () => {
    const fixture = createFakeMap();
    fixture.emit("style.load"); // The one style.load this mount will ever miss.
    expect(fixture.map.isStyleLoaded()).toBe(false);

    const mounted = render(element(fixture.map, 1));
    assertInstalled(fixture, 1);
    assertExclusive(fixture, "measured");

    const writes = fixture.sources.get(CELL_SOURCE_ID)!.setData.mock.calls.length;
    act(() => fixture.completeSources());
    expect(fixture.map.isStyleLoaded()).toBe(true);
    expect(fixture.calls.addSource).toHaveBeenCalledTimes(EXPECTED_SOURCES.length);
    expect(fixture.calls.addLayer).toHaveBeenCalledTimes(EXPECTED_LAYERS.length);
    expect(fixture.sources.get(CELL_SOURCE_ID)!.setData.mock.calls.length).toBe(writes);

    mounted.unmount();
    expect(fixture.sources.size).toBe(0);
    expect(fixture.layers.size).toBe(0);
    expect([...fixture.listeners.values()].every((set) => set.size === 0)).toBe(true);
    act(() => fixture.emit("style.load"));
    expect(fixture.sources.size).toBe(0);
  });

  it("waits for a genuinely unparsed style and then installs the latest pending props", () => {
    const fixture = createFakeMap(false);
    const mounted = render(element(fixture.map, 1));
    mounted.rerender(element(fixture.map, 2));
    act(() => useVegetationStore.setState({ source: "satellite" }));
    act(() => fixture.completeSources());
    expect(fixture.calls.addSource).not.toHaveBeenCalled();
    expect(fixture.calls.addLayer).not.toHaveBeenCalled();

    act(() => fixture.parseStyle());
    expect(fixture.map.isStyleLoaded()).toBe(false);
    assertInstalled(fixture, 2);
    assertExclusive(fixture, "satellite");
    mounted.unmount();
  });

  it("keeps one style.load listener in registration order across swaps and visibility", () => {
    const fixture = createFakeMap();
    const mounted = render(element(fixture.map, 1));
    const original = [...fixture.listeners.get("style.load")!][0];
    const later = vi.fn();
    fixture.calls.on("style.load", later);

    const cellSource = fixture.sources.get(CELL_SOURCE_ID);
    mounted.rerender(element(fixture.map, 2));
    expect(fixture.sources.get(CELL_SOURCE_ID)).toBe(cellSource);
    assertInstalled(fixture, 2);

    act(() => fixture.swapStyle());
    assertInstalled(fixture, 2);

    mounted.rerender(element(fixture.map, 2, false));
    expect(fixture.layers.size).toBe(0);
    expect(fixture.sources.size).toBe(0);
    act(() => fixture.swapStyle());
    expect(fixture.sources.size).toBe(0); // hidden: the listener runs but declines

    mounted.rerender(element(fixture.map, 2));
    assertInstalled(fixture, 2);
    expect([...fixture.listeners.get("style.load")!]).toEqual([original, later]);
    expect(fixture.calls.on.mock.calls.filter(([type]) => type === "style.load")).toHaveLength(2);
    expect(fixture.calls.off.mock.calls.filter(([type]) => type === "style.load")).toHaveLength(0);

    mounted.unmount();
    expect([...fixture.listeners.get("style.load")!]).toEqual([later]);
    expect(fixture.layers.size).toBe(0);
  });

  it("clears to an empty collection while still enabled", () => {
    const fixture = createFakeMap();
    const mounted = render(element(fixture.map, 1));
    mounted.rerender(element(fixture.map, 0));
    assertInstalled(fixture, 0);
    expect(fixture.sources.get(CELL_SOURCE_ID)?.setData).toHaveBeenCalledWith(cells(0));
    act(() => fixture.swapStyle());
    assertInstalled(fixture, 0);
    mounted.unmount();
  });

  it("cleans the previous map on replacement and installs on the next one", () => {
    const first = createFakeMap();
    const second = createFakeMap(false);
    const mounted = render(element(first.map, 1, false));
    const original = [...first.listeners.get("style.load")!][0];
    expect(first.sources.size).toBe(0);
    act(() => first.swapStyle());
    expect(first.sources.size).toBe(0);

    mounted.rerender(element(first.map, 1));
    assertInstalled(first, 1);
    expect([...first.listeners.get("style.load")!]).toEqual([original]);

    mounted.rerender(element(second.map, 2));
    expect(first.sources.size).toBe(0);
    expect(first.layers.size).toBe(0);
    expect([...first.listeners.values()].every((set) => set.size === 0)).toBe(true);
    act(() => first.emit("style.load"));
    expect(first.sources.size).toBe(0);

    expect(second.sources.size).toBe(0);
    act(() => second.parseStyle());
    assertInstalled(second, 2);
    mounted.unmount();
    expect(second.sources.size).toBe(0);
    expect([...second.listeners.values()].every((set) => set.size === 0)).toBe(true);
  });

  it("updates current data and paint in place rather than rebuilding the source", () => {
    const fixture = createFakeMap();
    const mounted = render(element(fixture.map, 1));
    const cellSource = fixture.sources.get(CELL_SOURCE_ID)!;
    fixture.calls.addSource.mockClear();
    fixture.calls.addLayer.mockClear();

    mounted.rerender(element(fixture.map, 2));
    expect(fixture.calls.addSource).not.toHaveBeenCalled();
    expect(fixture.calls.addLayer).not.toHaveBeenCalled();
    expect(fixture.sources.get(CELL_SOURCE_ID)).toBe(cellSource);
    expect(cellSource.setData).toHaveBeenCalledWith(cells(2));
    expect(fixture.paint.get(CELL_FILL_ID)?.["fill-opacity"]).toBe(0.375);
    if (RASTER_AVAILABLE) expect(fixture.paint.get(RASTER_ID)?.["raster-opacity"]).toBe(0.375);
    mounted.unmount();
  });

  it("keeps the measured and satellite encodings exclusive across a delayed admission and a swap", () => {
    const fixture = createFakeMap();
    fixture.emit("style.load");
    act(() => useVegetationStore.setState({ source: "satellite" }));
    const mounted = render(element(fixture.map, 1));
    assertInstalled(fixture, 1);
    assertExclusive(fixture, "satellite");

    act(() => useVegetationStore.setState({ source: "measured" }));
    assertExclusive(fixture, "measured");

    act(() => fixture.swapStyle());
    assertInstalled(fixture, 1);
    assertExclusive(fixture, "measured");

    act(() => useVegetationStore.setState({ source: "satellite" }));
    assertExclusive(fixture, "satellite");
    mounted.unmount();
  });

  it("attaches the composite raster once a period arrives after admission", () => {
    if (!RASTER_AVAILABLE) return;
    const fixture = createFakeMap();
    // Capabilities have not landed, so there is honestly no composite period to request.
    const mounted = render(element(fixture.map, 1, true, false));
    expect(fixture.sources.has(RASTER_SOURCE_ID)).toBe(false);
    expect([...fixture.layers.keys()].sort()).toEqual([CELL_FILL_ID, CELL_OUTLINE_ID].sort());

    mounted.rerender(element(fixture.map, 1, true, true));
    expect(fixture.sources.has(RASTER_SOURCE_ID)).toBe(true);
    expect(fixture.layers.has(RASTER_ID)).toBe(true);
    assertExclusive(fixture, "measured");
    mounted.unmount();
  });
});

describe("VegetationLayer scalar opt-in", () => {
  it("leaves the unset native rendering path intact", () => {
    vi.stubEnv("NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS", "");
    const fixture = createFakeMap();
    const mounted = render(<VegetationLayer map={fixture.map} />);
    expect(fixture.layers.has(CELL_FILL_ID)).toBe(true);
    expect(fixture.layers.has(FIELD_ID)).toBe(false);
    expect(fixture.layers.has(CELL_LABEL_ID)).toBe(false);
    mounted.unmount();
    expect(fixture.layers.size).toBe(0);
  });

  it("adds signed value labels, keeps native picking, and recreates one custom layer per style", () => {
    vi.stubEnv("NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS", "vegetation");
    const fixture = createFakeMap();
    const mounted = render(<VegetationLayer map={fixture.map} />);
    const labels = fixture.layers.get(CELL_LABEL_ID) as SymbolLayerSpecification;
    expect(labels.layout?.["text-field"]).toEqual(expect.arrayContaining([" NDVI"]));
    expect(JSON.stringify(labels.layout?.["text-field"])).toContain(">-0.01");
    expect(JSON.stringify(labels.layout?.["text-field"])).toContain("<0.01");
    expect(fixture.layers.get(CELL_FILL_ID)).toMatchObject({
      type: "fill",
      paint: { "fill-opacity-transition": { duration: 0, delay: 0 } },
    });
    expect(fixture.calls.addLayer).toHaveBeenCalledWith(expect.objectContaining({ id: FIELD_ID }), CELL_FILL_ID);

    mounted.rerender(<VegetationLayer map={fixture.map} opacityScale={0.5} />);
    // The scalar controller owns the source and the cell layout while it is installed.
    expect(fixture.sources.get(CELL_SOURCE_ID)?.setData).not.toHaveBeenCalled();
    expect(fixture.calls.addLayer.mock.calls.filter(([layer]) => layer.id === FIELD_ID)).toHaveLength(1);

    act(() => fixture.swapStyle());
    expect(fixture.calls.addLayer.mock.calls.filter(([layer]) => layer.id === FIELD_ID)).toHaveLength(2);
    mounted.unmount();
    expect(fixture.layers.size).toBe(0);
    expect([...fixture.listeners.values()].every((set) => set.size === 0)).toBe(true);
  });
});
