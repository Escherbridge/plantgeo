import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CustomLayerInterface, LayerSpecification, Map as MapLibreMap, SymbolLayerSpecification, VisibilitySpecification } from "maplibre-gl";
import { VegetationLayer } from "@/components/map/layers/VegetationLayer";

function harness() {
  const layers = new Map<string, LayerSpecification | CustomLayerInterface>();
  const layoutVisibility = new Map<string, VisibilitySpecification | undefined>();
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const events = new Map<string, Set<() => void>>();
  const map = {
    getLayer: (id: string) => layers.get(id), getSource: (id: string) => sources.get(id),
    addLayer: vi.fn((layer: LayerSpecification | CustomLayerInterface) => {
      layoutVisibility.set(layer.id, "layout" in layer ? layer.layout?.visibility : undefined);
      return layers.set(layer.id, layer);
    }),
    addSource: (id: string) => sources.set(id, { setData: vi.fn() }),
    removeLayer: (id: string) => layers.delete(id), removeSource: (id: string) => sources.delete(id),
    isStyleLoaded: () => true, getStyle: () => ({ layers: [] }),
    on: vi.fn((name: string, listener: () => void) => { const set = events.get(name) ?? new Set(); set.add(listener); events.set(name, set); }),
    off: vi.fn((name: string, listener: () => void) => events.get(name)?.delete(listener)),
    getLayoutProperty: (id: string) => layoutVisibility.get(id),
    setPaintProperty: vi.fn(), setLayoutProperty: vi.fn((id: string, _property: string, value: VisibilitySpecification) => layoutVisibility.set(id, value)),
  };
  return { map, layers, sources, events };
}
afterEach(() => vi.unstubAllEnvs());

describe("VegetationLayer scalar opt-in", () => {
  it("leaves the unset native rendering path intact", () => {
    vi.stubEnv("NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS", "");
    const h = harness();
    const mounted = render(<VegetationLayer map={h.map as unknown as MapLibreMap} />);
    expect(h.layers.has("vegetation-ndvi-cells-fill")).toBe(true);
    expect(h.layers.has("vegetation-ndvi-scalar-field")).toBe(false);
    expect(h.layers.has("vegetation-ndvi-cells-values")).toBe(false);
    mounted.unmount();
    expect(h.layers.size).toBe(0);
  });
  it("adds signed value labels, keeps native picking, and recreates one custom layer per style", () => {
    vi.stubEnv("NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS", "vegetation");
    const h = harness();
    const mounted = render(<VegetationLayer map={h.map as unknown as MapLibreMap} />);
    const labels = h.layers.get("vegetation-ndvi-cells-values") as SymbolLayerSpecification;
    expect(labels.layout?.["text-field"]).toEqual(expect.arrayContaining([" NDVI"]));
    expect(JSON.stringify(labels.layout?.["text-field"])).toContain(">-0.01");
    expect(JSON.stringify(labels.layout?.["text-field"])).toContain("<0.01");
    expect(h.layers.get("vegetation-ndvi-cells-fill")).toMatchObject({ type: "fill", paint: { "fill-opacity-transition": { duration: 0, delay: 0 } } });
    expect(h.map.addLayer).toHaveBeenCalledWith(expect.objectContaining({ id: "vegetation-ndvi-scalar-field" }), "vegetation-ndvi-cells-fill");
    mounted.rerender(<VegetationLayer map={h.map as unknown as MapLibreMap} opacityScale={0.5} />);
    expect(h.sources.get("vegetation-ndvi-cells")?.setData).not.toHaveBeenCalled();
    expect(h.map.addLayer.mock.calls.filter(([layer]) => layer.id === "vegetation-ndvi-scalar-field")).toHaveLength(1);
    h.layers.clear(); h.sources.clear();
    act(() => h.events.get("style.load")?.forEach((listener) => listener()));
    expect(h.map.addLayer.mock.calls.filter(([layer]) => layer.id === "vegetation-ndvi-scalar-field")).toHaveLength(2);
    mounted.unmount();
    expect(h.layers.size).toBe(0);
    expect([...h.events.values()].every((set) => set.size === 0)).toBe(true);
  });
});
