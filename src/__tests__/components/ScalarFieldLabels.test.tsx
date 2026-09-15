import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import { ClimateFieldLayer } from "@/components/map/layers/ClimateFieldLayer";
import { SoilFieldLayer } from "@/components/map/layers/SoilFieldLayer";

type Listener = (event?: unknown) => void;

/**
 * PARSED style (`getStyle()` truthy) is what admits addSource/addLayer; SOURCE readiness
 * (`isStyleLoaded()`) is a strictly later, unrelated milestone that only tile completion
 * reaches. The two are modelled separately on purpose -- a fixture whose map is globally ready
 * cannot tell the two apart and so proves nothing about late mounts. See map/AGENTS.md.
 *
 * Tile completion is emitted as `sourcedata` ONLY. Fabricating a `style.load` there would hand
 * the component a second admission path production does not give it.
 */
function recordingMap(parsed = true) {
  let styleParsed = parsed;
  let sourcesLoaded = false;
  const layers = new Map<string, LayerSpecification>();
  const sources = new Map<string, { data: unknown; setData: ReturnType<typeof vi.fn> }>();
  const listeners = new Map<string, Set<Listener>>();
  const requireParsed = () => {
    if (!styleParsed) throw new Error("Style is not done loading.");
  };
  const emit = (event: string) => {
    for (const listener of [...(listeners.get(event) ?? [])]) listener();
  };
  const map = {
    getLayer: (id: string) => layers.get(id),
    getSource: (id: string) => sources.get(id),
    addLayer: vi.fn((layer: LayerSpecification) => {
      requireParsed();
      if (layers.has(layer.id)) throw new Error(`Duplicate layer ${layer.id}`);
      layers.set(layer.id, layer);
    }),
    addSource: vi.fn((id: string, source: { data: unknown }) => {
      requireParsed();
      if (sources.has(id)) throw new Error(`Duplicate source ${id}`);
      const entry = { data: source.data, setData: vi.fn() };
      entry.setData.mockImplementation((data: unknown) => { entry.data = data; });
      sources.set(id, entry);
    }),
    removeLayer: (id: string) => layers.delete(id),
    removeSource: (id: string) => sources.delete(id),
    isStyleLoaded: () => styleParsed && sourcesLoaded,
    getStyle: () => (styleParsed ? { layers: [...layers.values()] } : undefined),
    on: vi.fn((event: string, callback: Listener) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)?.add(callback);
    }),
    off: vi.fn((event: string, callback: Listener) => listeners.get(event)?.delete(callback)),
    setPaintProperty: vi.fn((id: string, property: string, value: unknown) => {
      const layer = layers.get(id) as { paint?: Record<string, unknown> } | undefined;
      if (!layer) throw new Error(`Missing layer ${id}`);
      layer.paint = { ...(layer.paint ?? {}), [property]: value };
    }),
  };
  return {
    map: map as unknown as MapLibreMap,
    layers,
    sources,
    listeners,
    recorder: map,
    emit,
    /** The style finishes PARSING. Sources are still loading. */
    parseStyle: () => { styleParsed = true; sourcesLoaded = false; emit("style.load"); },
    /** Tiles land. No style event -- this is `sourcedata` and nothing else. */
    completeSources: () => { sourcesLoaded = true; emit("sourcedata"); },
    /** Basemap swap: MapLibre drops every custom source/layer and re-parses. */
    swapStyle: () => {
      layers.clear();
      sources.clear();
      styleParsed = true;
      sourcesLoaded = false;
      emit("style.load");
    },
  };
}

const served: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [{
  type: "Feature", geometry: { type: "Polygon", coordinates: [[[-120, 44], [-119, 44], [-119, 45], [-120, 45], [-120, 44]]] },
  properties: { value: 0.234, aggregated: true, observedDay: "2026-08-01" },
}] };

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

type Kind = "soil" | "climate";

const sourceIdFor = (kind: Kind) =>
  kind === "soil" ? "soil-moisture-field" : "climate-field-air-temperature";

function elementFor(
  kind: Kind,
  map: MapLibreMap,
  opacityScale: number,
  geojson: GeoJSON.FeatureCollection | null,
  visible = true
) {
  return kind === "soil"
    ? <SoilFieldLayer map={map} measure="moisture" geojson={geojson} opacityScale={opacityScale} visible={visible} />
    : <ClimateFieldLayer map={map} signal="air-temperature" renderForm="field" zoomTier={5} geojson={geojson} opacityScale={opacityScale} visible={visible} />;
}

describe("scalar field label lifecycle", () => {
  it.each(["soil", "climate"] as const)("shares the exact served %s source through updates, reload, and teardown", (kind) => {
    const { map, layers, sources, recorder, swapStyle } = recordingMap();
    const source = sourceIdFor(kind);
    const labelId = `${source}-value-labels`;
    const element = (opacityScale: number, geojson: GeoJSON.FeatureCollection | null, visible = true) =>
      elementFor(kind, map, opacityScale, geojson, visible);
    const mounted = render(element(1, served));
    expect(sources.size).toBe(1);
    expect(recorder.addSource).toHaveBeenCalledWith(source, expect.objectContaining({ data: served }));
    expect(layers.get(labelId)).toMatchObject({ type: "symbol", source });
    mounted.rerender(element(0.4, null));
    expect(sources.get(source)?.setData).toHaveBeenLastCalledWith(EMPTY);
    expect(recorder.setPaintProperty).toHaveBeenCalledWith(labelId, "text-opacity", 0.4);
    act(swapStyle);
    expect(layers.get(labelId)).toMatchObject({ paint: { "text-opacity": 0.4 } });
    expect(sources.size).toBe(1);
    mounted.rerender(element(0.4, null, false));
    expect(layers.size).toBe(0);
    expect(sources.size).toBe(0);
    mounted.unmount();
  });

  it.each(["soil", "climate"] as const)("installs the %s field on a delayed mount while sources are still loading", (kind) => {
    const fixture = recordingMap();
    // `style.load` has ALREADY fired before this component exists: the exact shape of the bug.
    fixture.emit("style.load");
    expect(fixture.map.isStyleLoaded()).toBe(false);
    const mounted = render(elementFor(kind, fixture.map, 1, served));
    const source = sourceIdFor(kind);
    expect(fixture.sources.has(source)).toBe(true);
    expect(fixture.layers.has(`${source}-value-labels`)).toBe(true);
    const addLayerCalls = fixture.recorder.addLayer.mock.calls.length;
    // Tile completion arrives as `sourcedata` and must not be needed to install anything.
    act(() => fixture.completeSources());
    expect(fixture.map.isStyleLoaded()).toBe(true);
    expect(fixture.recorder.addSource).toHaveBeenCalledTimes(1);
    expect(fixture.recorder.addLayer.mock.calls).toHaveLength(addLayerCalls);
    mounted.unmount();
    expect(fixture.sources.size).toBe(0);
    expect(fixture.layers.size).toBe(0);
  });

  it.each(["soil", "climate"] as const)("waits for a genuinely unparsed %s style and then installs the latest pending props", (kind) => {
    const fixture = recordingMap(false);
    const mounted = render(elementFor(kind, fixture.map, 1, EMPTY));
    mounted.rerender(elementFor(kind, fixture.map, 0.4, served));
    act(() => fixture.completeSources());
    // An unparsed style rejects addSource/addLayer -- nothing may have been attempted.
    expect(fixture.recorder.addSource).not.toHaveBeenCalled();
    expect(fixture.recorder.addLayer).not.toHaveBeenCalled();
    act(() => fixture.parseStyle());
    expect(fixture.map.isStyleLoaded()).toBe(false);
    const source = sourceIdFor(kind);
    expect(fixture.recorder.addSource).toHaveBeenCalledWith(source, expect.objectContaining({ data: served }));
    expect(fixture.layers.get(`${source}-value-labels`)).toMatchObject({ paint: { "text-opacity": 0.4 } });
    mounted.unmount();
  });

  it.each(["soil", "climate"] as const)("keeps one stable %s style.load listener across swaps and hidden->shown", (kind) => {
    const fixture = recordingMap();
    const mounted = render(elementFor(kind, fixture.map, 1, served, false));
    const own = [...(fixture.listeners.get("style.load") ?? [])][0];
    expect(own).toBeTypeOf("function");
    expect(fixture.sources.size).toBe(0);
    const later = vi.fn();
    fixture.recorder.on("style.load", later);
    act(() => fixture.swapStyle());
    // Hidden means hidden, even when the style re-parses under it.
    expect(fixture.sources.size).toBe(0);
    mounted.rerender(elementFor(kind, fixture.map, 1, served));
    expect(fixture.sources.size).toBe(1);
    act(() => fixture.swapStyle());
    expect(fixture.sources.size).toBe(1);
    // Registration order is load-bearing for stacking: our handler must still be FIRST.
    expect([...(fixture.listeners.get("style.load") ?? [])]).toEqual([own, later]);
    expect(fixture.recorder.on.mock.calls.filter(([event]) => event === "style.load")).toHaveLength(2);
    expect(fixture.recorder.off.mock.calls.filter(([event]) => event === "style.load")).toHaveLength(0);
    mounted.unmount();
    expect([...(fixture.listeners.get("style.load") ?? [])]).toEqual([later]);
  });

  it.each(["soil", "climate"] as const)("detaches the previous %s map and installs on the replacement", (kind) => {
    const first = recordingMap();
    const second = recordingMap(false);
    const mounted = render(elementFor(kind, first.map, 1, served));
    expect(first.sources.size).toBe(1);
    mounted.rerender(elementFor(kind, second.map, 1, served));
    expect(first.sources.size).toBe(0);
    expect(first.layers.size).toBe(0);
    expect([...first.listeners.values()].every((set) => set.size === 0)).toBe(true);
    act(() => first.emit("style.load"));
    expect(first.sources.size).toBe(0);
    expect(second.sources.size).toBe(0);
    act(() => second.parseStyle());
    expect(second.sources.has(sourceIdFor(kind))).toBe(true);
    mounted.unmount();
    expect(second.sources.size).toBe(0);
    expect(second.layers.size).toBe(0);
  });

  it("preserves the soil measure ids and the aggregated-cell outline expression under a scale", () => {
    const fixture = recordingMap();
    fixture.emit("style.load");
    const mounted = render(
      <SoilFieldLayer map={fixture.map} measure="moisture" geojson={served} opacityScale={0.5} />
    );
    expect([...fixture.layers.keys()].sort()).toEqual([
      "soil-moisture-field-fill",
      "soil-moisture-field-outline",
      "soil-moisture-field-value-labels",
    ]);
    const outline = fixture.layers.get("soil-moisture-field-outline") as unknown as { paint: Record<string, unknown> };
    // `0 * f = 0` keeps the aggregated arm exactly zero; a scalar write would erase the case.
    expect(outline.paint["line-opacity"]).toEqual([
      "*", ["case", ["==", ["get", "aggregated"], true], 0, 0.25], 0.5,
    ]);
    const fill = fixture.layers.get("soil-moisture-field-fill") as unknown as { paint: Record<string, unknown> };
    expect(fill.paint["fill-opacity"]).toBeCloseTo(0.7 * 0.5);
    mounted.unmount();
  });

  it("keeps the soil style.load registration untouched when the measure changes", () => {
    const fixture = recordingMap();
    const mounted = render(<SoilFieldLayer map={fixture.map} measure="moisture" geojson={served} />);
    const own = [...(fixture.listeners.get("style.load") ?? [])][0];
    // A neighbour registers AFTER us, so any re-registration would put us behind it -- which is
    // exactly the MapLibre stacking inversion this shape exists to prevent.
    const later = vi.fn();
    fixture.recorder.on("style.load", later);

    mounted.rerender(<SoilFieldLayer map={fixture.map} measure="temperature" geojson={served} />);

    // The callbacks read ids/ramp/measure off propsRef, so they keep their identity and the
    // listener effect never re-runs: one registration for the life of the map, still first.
    expect(fixture.recorder.off.mock.calls.filter(([event]) => event === "style.load")).toHaveLength(0);
    expect(fixture.recorder.on.mock.calls.filter(([event]) => event === "style.load")).toHaveLength(2);
    expect([...(fixture.listeners.get("style.load") ?? [])]).toEqual([own, later]);
    // The measure swap still rebuilds: the new ids are installed and the old ones are gone.
    expect([...fixture.layers.keys()].sort()).toEqual([
      "soil-temperature-field-fill",
      "soil-temperature-field-outline",
      "soil-temperature-field-value-labels",
    ]);
    expect([...fixture.sources.keys()]).toEqual(["soil-temperature-field"]);
    mounted.unmount();
  });

  it("writes current data and paint on an existing soil source without rebuilding it", () => {
    const fixture = recordingMap();
    const mounted = render(<SoilFieldLayer map={fixture.map} measure="moisture" geojson={served} opacityScale={1} />);
    const source = fixture.sources.get("soil-moisture-field")!;
    mounted.rerender(<SoilFieldLayer map={fixture.map} measure="moisture" geojson={EMPTY} opacityScale={0.5} />);
    expect(fixture.sources.get("soil-moisture-field")).toBe(source);
    expect(source.setData).toHaveBeenLastCalledWith(EMPTY);
    expect(fixture.recorder.addSource).toHaveBeenCalledTimes(1);
    expect(fixture.recorder.setPaintProperty).toHaveBeenCalledWith("soil-moisture-field-fill", "fill-opacity", 0.7 * 0.5);
    expect(fixture.recorder.setPaintProperty).toHaveBeenCalledWith("soil-moisture-field-value-labels", "text-opacity", 0.5);
    mounted.unmount();
  });

  it("removes value labels when changing a measured field to representative isobands", () => {
    const { map, layers } = recordingMap();
    const mounted = render(<ClimateFieldLayer map={map} signal="air-temperature" renderForm="field" zoomTier={9} geojson={served} />);
    expect(layers.has("climate-field-air-temperature-value-labels")).toBe(true);
    mounted.rerender(<ClimateFieldLayer map={map} signal="air-temperature" renderForm="isoline" zoomTier={9} geojson={served} />);
    expect([...layers.values()].map(layer => layer.type)).toEqual(["fill", "line"]);
    mounted.unmount();
  });
});
