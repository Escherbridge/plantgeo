import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import { ClimateFieldLayer } from "@/components/map/layers/ClimateFieldLayer";
import { SoilFieldLayer } from "@/components/map/layers/SoilFieldLayer";

function recordingMap() {
  const layers = new Map<string, LayerSpecification>();
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const listeners = new Map<string, Set<() => void>>();
  const map = {
    getLayer: (id: string) => layers.get(id),
    getSource: (id: string) => sources.get(id),
    addLayer: (layer: LayerSpecification) => layers.set(layer.id, layer),
    addSource: vi.fn((id: string, _source: unknown) => sources.set(id, { setData: vi.fn() })),
    removeLayer: (id: string) => layers.delete(id),
    removeSource: (id: string) => sources.delete(id),
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [...layers.values()] }),
    on: (event: string, callback: () => void) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)?.add(callback);
    },
    off: (event: string, callback: () => void) => listeners.get(event)?.delete(callback),
    setPaintProperty: vi.fn(),
  };
  return { map: map as unknown as MapLibreMap, layers, sources, recorder: map, reload: () => {
    layers.clear();
    sources.clear();
    listeners.get("style.load")?.forEach(callback => callback());
  } };
}

const served: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [{
  type: "Feature", geometry: { type: "Polygon", coordinates: [[[-120, 44], [-119, 44], [-119, 45], [-120, 45], [-120, 44]]] },
  properties: { value: 0.234, aggregated: true, observedDay: "2026-08-01" },
}] };

describe("scalar field label lifecycle", () => {
  it.each(["soil", "climate"] as const)("shares the exact served %s source through updates, reload, and teardown", (kind) => {
    const { map, layers, sources, recorder, reload } = recordingMap();
    const source = kind === "soil" ? "soil-moisture-field" : "climate-field-air-temperature";
    const labelId = `${source}-value-labels`;
    const element = (opacityScale: number, geojson: GeoJSON.FeatureCollection | null, visible = true) => kind === "soil"
      ? <SoilFieldLayer map={map} measure="moisture" geojson={geojson} opacityScale={opacityScale} visible={visible} />
      : <ClimateFieldLayer map={map} signal="air-temperature" renderForm="field" zoomTier={5} geojson={geojson} opacityScale={opacityScale} visible={visible} />;
    const mounted = render(element(1, served));
    expect(sources.size).toBe(1);
    expect(recorder.addSource).toHaveBeenCalledWith(source, expect.objectContaining({ data: served }));
    expect(layers.get(labelId)).toMatchObject({ type: "symbol", source });
    mounted.rerender(element(0.4, null));
    expect(sources.get(source)?.setData).toHaveBeenLastCalledWith({ type: "FeatureCollection", features: [] });
    expect(recorder.setPaintProperty).toHaveBeenCalledWith(labelId, "text-opacity", 0.4);
    act(reload);
    expect(layers.get(labelId)).toMatchObject({ paint: { "text-opacity": 0.4 } });
    expect(sources.size).toBe(1);
    mounted.rerender(element(0.4, null, false));
    expect(layers.size).toBe(0);
    expect(sources.size).toBe(0);
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
