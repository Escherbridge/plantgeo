import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Map as MapLibreMap } from "maplibre-gl";
import { SoilLayer } from "@/components/map/layers/SoilLayer";
import type { PublishedSoilRaster } from "@/lib/map/soil-raster";

const release: PublishedSoilRaster = {
  property: "soc",
  unit: "g/kg",
  scaleDivisor: 10,
  valueMin: 5,
  valueMax: 460,
  colorRamp: [{ value: 5, color: "#fff" }, { value: 60, color: "#000" }],
  archiveUrl: "https://tiles.example.test/raster/soil/soc.pmtiles",
  minZoom: 0,
  maxZoom: 10,
  attribution: "ISRIC SoilGrids",
  sourceName: "SoilGrids",
  sourceRelease: "2.0",
  licenseName: "CC-BY 4.0",
  bounds: [-125, 42, -111, 49],
};

function recordingMap() {
  const sources = new Map<string, unknown>();
  const layers = new Map<string, { id: string; paint?: Record<string, unknown> }>();
  const map = {
    getStyle: () => ({ layers: [] }),
    isStyleLoaded: () => true,
    getSource: (id: string) => sources.get(id),
    getLayer: (id: string) => layers.get(id),
    addSource: vi.fn((id: string, source: unknown) => sources.set(id, source)),
    addLayer: vi.fn((layer: { id: string; paint?: Record<string, unknown> }) => layers.set(layer.id, layer)),
    removeLayer: vi.fn((id: string) => layers.delete(id)),
    removeSource: vi.fn((id: string) => sources.delete(id)),
    setPaintProperty: vi.fn((id: string, property: string, value: unknown) => {
      const layer = layers.get(id);
      if (layer) layer.paint = { ...layer.paint, [property]: value };
    }),
    once: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
  };
  return { map: map as unknown as MapLibreMap, sources, layers, recorder: map };
}

describe("SoilLayer", () => {
  it("draws a property-qualified PMTiles raster and applies independent opacity", () => {
    const fixture = recordingMap();
    const mounted = render(
      <SoilLayer map={fixture.map} release={release} visible opacityScale={0.5} />
    );

    expect(fixture.recorder.addSource).toHaveBeenCalledWith(
      "soilgrids-soc-source",
      expect.objectContaining({
        type: "raster",
        url: "pmtiles://https://tiles.example.test/raster/soil/soc.pmtiles",
        minzoom: 0,
        maxzoom: 10,
        bounds: release.bounds,
      })
    );
    expect(fixture.layers.get("soilgrids-soc-layer")).toMatchObject({
      id: "soilgrids-soc-layer",
      paint: { "raster-opacity": 0.35 },
    });

    mounted.rerender(
      <SoilLayer map={fixture.map} release={release} visible opacityScale={0.25} />
    );
    expect(fixture.recorder.setPaintProperty).toHaveBeenLastCalledWith(
      "soilgrids-soc-layer",
      "raster-opacity",
      0.175
    );

    const replacement = {
      ...release,
      archiveUrl: "https://tiles.example.test/raster/soil/soc-v2.pmtiles",
    };
    mounted.rerender(
      <SoilLayer map={fixture.map} release={replacement} visible opacityScale={0.25} />
    );
    expect(fixture.recorder.removeSource).toHaveBeenCalledWith("soilgrids-soc-source");
    expect(fixture.recorder.addSource).toHaveBeenLastCalledWith(
      "soilgrids-soc-source",
      expect.objectContaining({
        url: "pmtiles://https://tiles.example.test/raster/soil/soc-v2.pmtiles",
      })
    );

    mounted.unmount();
    expect(fixture.sources.has("soilgrids-soc-source")).toBe(false);
    expect(fixture.layers.has("soilgrids-soc-layer")).toBe(false);
  });
});
