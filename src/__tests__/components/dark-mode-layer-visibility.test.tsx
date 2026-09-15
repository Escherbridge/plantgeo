import { describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { FireLayer } from "@/components/map/layers/FireLayer";
import { WaterLayer } from "@/components/map/layers/WaterLayer";
import type { FireDetectionCollection } from "@/lib/environmental/parquet-fire-presentation";
import type { WaterGaugeCell } from "@/lib/environmental/parquet-presentation";
import type { GroundwaterWell, WaterGauge } from "@/lib/environmental/water";

type Listener = (event?: unknown) => void;
type TestLayer = { id: string; type: string; source: string; paint: Record<string, unknown> };
type TestSource = { data: GeoJSON.FeatureCollection; setData: ReturnType<typeof vi.fn> };

// Parsed-style admission is independent of tile readiness; see map/AGENTS.md.
function createFakeMap(parsed = true) {
  let styleParsed = parsed;
  let sourcesLoaded = false;
  const sources = new Map<string, TestSource>();
  const layers = new Map<string, TestLayer>();
  const listeners = new Map<string, Set<Listener>>();
  const key = (type: string, layer?: string) => layer ? `${type}:${layer}` : type;
  const requireParsed = () => { if (!styleParsed) throw new Error("Style is not done loading."); };
  const emit = (type: string, event?: unknown, layer?: string) => {
    for (const listener of [...(listeners.get(key(type, layer)) ?? [])]) listener(event);
  };
  const map = {
    on: vi.fn((type: string, a: Listener | string, b?: Listener) => {
      const eventKey = key(type, typeof a === "string" ? a : undefined);
      const listener = typeof a === "string" ? b! : a;
      const current = listeners.get(eventKey) ?? new Set<Listener>();
      current.add(listener); listeners.set(eventKey, current);
    }),
    off: vi.fn((type: string, a: Listener | string, b?: Listener) => {
      listeners.get(key(type, typeof a === "string" ? a : undefined))?.delete(typeof a === "string" ? b! : a);
    }),
    getStyle: () => styleParsed ? { version: 8, sources: {}, layers: [...layers.values()] } : undefined,
    isStyleLoaded: () => styleParsed && sourcesLoaded,
    getSource: (id: string) => sources.get(id),
    getLayer: (id: string) => layers.get(id),
    addSource: vi.fn((id: string, input: { data: GeoJSON.FeatureCollection }) => {
      requireParsed();
      if (sources.has(id)) throw new Error(`Duplicate source ${id}`);
      const source: TestSource = { data: input.data, setData: vi.fn() };
      source.setData.mockImplementation((data: GeoJSON.FeatureCollection) => { source.data = data; });
      sources.set(id, source);
    }),
    addLayer: vi.fn((layer: TestLayer) => {
      requireParsed();
      if (layers.has(layer.id)) throw new Error(`Duplicate layer ${layer.id}`);
      layers.set(layer.id, { ...layer, paint: { ...layer.paint } });
    }),
    removeLayer: vi.fn((id: string) => { layers.delete(id); }),
    removeSource: vi.fn((id: string) => { sources.delete(id); }),
    setPaintProperty: vi.fn((id: string, property: string, value: unknown) => {
      const layer = layers.get(id);
      if (!layer) throw new Error(`Missing layer ${id}`);
      layer.paint[property] = value;
    }),
  };
  return {
    map: map as unknown as MapLibreMap, calls: map, sources, layers, listeners, emit,
    parseStyle: () => { styleParsed = true; sourcesLoaded = false; emit("style.load"); },
    completeSources: () => { sourcesLoaded = true; emit("sourcedata"); },
    swapStyle: () => { sources.clear(); layers.clear(); styleParsed = true; sourcesLoaded = false; emit("style.load"); },
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

// Existing published marker fixture retains its declared aggregate support.
const FIRE_GEOJSON: FireDetectionCollection = {
  type: "FeatureCollection",
  features: [{
    type: "Feature", geometry: { type: "Point", coordinates: [-116.2, 43.6] },
    properties: {
      detectionCount: 4, frpSum: 120.5, frpObservationCount: 4,
      highConfidenceDetectionCount: 2, observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T19:12:00Z", zoomTier: 9,
      supportKind: "aggregate_cell", supportId: "z09:-116.2:43.6",
      cellWidthDegrees: null, cellHeightDegrees: null,
    },
  }],
};

function fireData(revision: number): FireDetectionCollection {
  return revision === 0 ? { type: "FeatureCollection", features: [] } : {
    ...FIRE_GEOJSON,
    features: FIRE_GEOJSON.features.map((feature) => ({
      ...feature, properties: { ...feature.properties, detectionCount: revision * 4 },
    })),
  };
}

function waterData(revision: number) {
  const gauges: WaterGauge[] = revision === 0 ? [] : [{
    siteNo: "gauge-1", siteName: "QA gauge", lon: -116.2, lat: 43.6,
    flowCfs: revision * 120, percentile: null, condition: "unknown", trend: null,
    updatedAt: "2026-08-28T19:12:00Z",
  }];
  const wells: GroundwaterWell[] = revision === 0 ? [] : [{
    siteNo: "well-1", siteName: "QA well", lon: -116.2, lat: 43.6,
    depthFt: revision * 20, trend: "stable", updatedAt: "2026-08-28T19:12:00Z",
  }];
  const aggregateCells: WaterGaugeCell[] = revision === 0 ? [] : [{
    longitude: -116.2, latitude: 43.6, flowCfs: revision * 60,
    observedAt: "2026-08-28T19:12:00Z", observedDay: "2026-08-28", source: "QA fixture",
    support: {
      zoomTier: 5, supportKind: "aggregate_cell", supportId: "qa-cell", origin: "cell_origin",
      cellWidthDegrees: 0.2, cellHeightDegrees: 0.2, cellOriginDegrees: [-116.2, 43.6],
      aggregationMethod: "mean", contributorCount: 2,
      provenance: { sourceLayer: "water-gauges", observedDay: "2026-08-28", newestObservedAt: "2026-08-28T19:12:00Z", attribution: "QA fixture" },
    },
  }];
  return { gauges, aggregateCells, wells };
}

const scenarios = [
  {
    name: "FireLayer", sourceIds: ["published-fire-source"],
    layerIds: ["published-fire-cells-fill", "published-fire-circles", "published-fire-outlines"],
    clickIds: ["published-fire-cells-fill", "published-fire-circles"],
    element: (map: MapLibreMap, revision: number, visible = true) => <FireLayer map={map} visible={visible} geojson={fireData(revision)} opacityScale={revision === 2 ? 0.4 : 1} />,
    assertData: (fixture: FakeMap, revision: number) => {
      expect(fixture.sources.get("published-fire-source")?.data).toEqual(fireData(revision));
      const opacity = revision === 2 ? 0.85 * 0.4 : 0.85;
      expect(fixture.layers.get("published-fire-cells-fill")?.paint["fill-opacity"]).toBe(opacity);
      expect(fixture.layers.get("published-fire-circles")?.paint["circle-opacity"]).toBe(opacity);
      expect(fixture.layers.get("published-fire-outlines")?.paint).toMatchObject({ "circle-opacity": 0, "circle-stroke-opacity": opacity });
    },
  },
  {
    name: "WaterLayer", sourceIds: ["water-gauges", "water-gauge-cells", "groundwater-wells"],
    layerIds: ["water-gauges-circle", "water-gauge-cells-fill", "water-gauge-cells-circle", "groundwater-wells-circle"],
    clickIds: ["water-gauges-circle", "water-gauge-cells-fill", "water-gauge-cells-circle", "groundwater-wells-circle"],
    element: (map: MapLibreMap, revision: number, visible = true) => <WaterLayer map={map} visible={visible} {...waterData(revision)} opacityScale={revision === 2 ? 0.4 : 1} />,
    assertData: (fixture: FakeMap, revision: number) => {
      const gauge = fixture.sources.get("water-gauges")?.data.features;
      const cells = fixture.sources.get("water-gauge-cells")?.data.features;
      const wells = fixture.sources.get("groundwater-wells")?.data.features;
      if (revision === 0) { expect(gauge).toEqual([]); expect(cells).toEqual([]); expect(wells).toEqual([]); }
      else {
        expect(gauge).toHaveLength(1); expect(cells).toHaveLength(1); expect(wells).toHaveLength(1);
        expect(gauge?.[0].properties).toMatchObject({ siteNo: "gauge-1", flowCfs: revision * 120 });
        expect(cells?.[0]).toMatchObject({ geometry: { type: "Polygon" }, properties: { flowCfs: revision * 60, gaugeCount: 2 } });
        expect(wells?.[0].properties).toMatchObject({ siteNo: "well-1", depthFt: revision * 20 });
      }
      const scale = revision === 2 ? 0.4 : 1;
      expect(fixture.layers.get("water-gauges-circle")?.paint["circle-opacity"]).toBe(0.9 * scale);
      expect(fixture.layers.get("water-gauge-cells-fill")?.paint["fill-opacity"]).toBe(0.9 * scale);
      expect(fixture.layers.get("water-gauge-cells-circle")?.paint["circle-opacity"]).toBe(0.9 * scale);
      expect(fixture.layers.get("groundwater-wells-circle")?.paint["circle-opacity"]).toBe(0.85 * scale);
    },
  },
];

describe.each(scenarios)("$name parsed-style admission", (scenario) => {
  const assertInstalled = (fixture: FakeMap, revision: number) => {
    expect([...fixture.sources.keys()].sort()).toEqual([...scenario.sourceIds].sort());
    expect([...fixture.layers.keys()].sort()).toEqual([...scenario.layerIds].sort());
    scenario.assertData(fixture, revision);
    for (const id of scenario.clickIds) expect(fixture.listeners.get(`click:${id}`)?.size).toBe(1);
  };

  it("installs on delayed mount before source-only completion, without a synthetic styledata event", () => {
    const fixture = createFakeMap();
    fixture.emit("style.load");
    expect(fixture.map.isStyleLoaded()).toBe(false);
    const mounted = render(scenario.element(fixture.map, 1));
    assertInstalled(fixture, 1);
    const writes = [...fixture.sources.values()].map((source) => source.setData.mock.calls.length);
    act(() => fixture.completeSources());
    expect(fixture.map.isStyleLoaded()).toBe(true);
    expect(fixture.calls.addSource).toHaveBeenCalledTimes(scenario.sourceIds.length);
    expect(fixture.calls.addLayer).toHaveBeenCalledTimes(scenario.layerIds.length);
    expect([...fixture.sources.values()].map((source) => source.setData.mock.calls.length)).toEqual(writes);
    mounted.unmount();
    expect(fixture.sources.size).toBe(0); expect(fixture.layers.size).toBe(0);
    expect([...fixture.listeners.values()].every((listeners) => listeners.size === 0)).toBe(true);
    act(() => fixture.emit("style.load"));
    expect(fixture.sources.size).toBe(0);
  });

  it("waits for a genuinely unparsed style and installs current pending data and opacity", () => {
    const fixture = createFakeMap(false);
    const mounted = render(scenario.element(fixture.map, 1));
    mounted.rerender(scenario.element(fixture.map, 2));
    act(() => fixture.completeSources());
    expect(fixture.calls.addSource).not.toHaveBeenCalled();
    expect(fixture.calls.addLayer).not.toHaveBeenCalled();
    act(() => fixture.parseStyle());
    expect(fixture.map.isStyleLoaded()).toBe(false);
    assertInstalled(fixture, 2);
    mounted.unmount();
  });

  it("keeps listener order, current source families and empty data across swaps and visibility", () => {
    const fixture = createFakeMap();
    const mounted = render(scenario.element(fixture.map, 1));
    const original = [...fixture.listeners.get("style.load")!][0];
    const later = vi.fn(); fixture.calls.on("style.load", later);
    const sourceIdentities = [...fixture.sources.values()];
    mounted.rerender(scenario.element(fixture.map, 2));
    [...fixture.sources.values()].forEach((source, index) => expect(source).toBe(sourceIdentities[index]));
    assertInstalled(fixture, 2);
    act(() => fixture.swapStyle()); assertInstalled(fixture, 2);
    mounted.rerender(scenario.element(fixture.map, 0)); assertInstalled(fixture, 0);
    act(() => fixture.swapStyle()); assertInstalled(fixture, 0);
    mounted.rerender(scenario.element(fixture.map, 2, false));
    expect(fixture.layers.size).toBe(0); expect(fixture.sources.size).toBe(0);
    for (const id of scenario.clickIds) expect(fixture.listeners.get(`click:${id}`)?.size).toBe(0);
    act(() => fixture.swapStyle());
    expect(fixture.sources.size).toBe(0);
    mounted.rerender(scenario.element(fixture.map, 2));
    assertInstalled(fixture, 2);
    expect([...fixture.listeners.get("style.load")!]).toEqual([original, later]);
    expect(fixture.calls.on.mock.calls.filter(([type]) => type === "style.load")).toHaveLength(2);
    expect(fixture.calls.off.mock.calls.filter(([type]) => type === "style.load")).toHaveLength(0);
    mounted.unmount();
    expect([...fixture.listeners.get("style.load")!]).toEqual([later]);
    expect(fixture.layers.size).toBe(0); expect(fixture.sources.size).toBe(0);
  });

  it("keeps an initially hidden listener in place and cleans the previous map on replacement", () => {
    const first = createFakeMap(); const second = createFakeMap(false);
    const mounted = render(scenario.element(first.map, 1, false));
    const original = [...first.listeners.get("style.load")!][0];
    expect(first.sources.size).toBe(0);
    act(() => first.swapStyle()); expect(first.sources.size).toBe(0);
    mounted.rerender(scenario.element(first.map, 1)); assertInstalled(first, 1);
    expect([...first.listeners.get("style.load")!]).toEqual([original]);
    mounted.rerender(scenario.element(second.map, 2));
    expect(first.sources.size).toBe(0); expect(first.layers.size).toBe(0);
    expect([...first.listeners.values()].every((listeners) => listeners.size === 0)).toBe(true);
    act(() => first.emit("style.load")); expect(first.sources.size).toBe(0);
    expect(second.sources.size).toBe(0);
    act(() => second.parseStyle()); assertInstalled(second, 2);
    mounted.unmount();
    expect(second.sources.size).toBe(0); expect(second.layers.size).toBe(0);
    expect([...second.listeners.values()].every((listeners) => listeners.size === 0)).toBe(true);
  });
});

it("preserves current named gauge and well selection callbacks through source readiness and swaps", () => {
  const fixture = createFakeMap();
  const onGaugeClick = vi.fn(); const onWellClick = vi.fn();
  const mounted = render(<WaterLayer map={fixture.map} {...waterData(1)} onGaugeClick={onGaugeClick} onWellClick={onWellClick} />);
  mounted.rerender(<WaterLayer map={fixture.map} {...waterData(2)} onGaugeClick={onGaugeClick} onWellClick={onWellClick} />);
  act(() => fixture.completeSources()); act(() => fixture.swapStyle());
  act(() => fixture.emit("click", { features: [{ properties: { siteNo: "gauge-1" } }] }, "water-gauges-circle"));
  act(() => fixture.emit("click", { features: [{ properties: { siteNo: "well-1" } }] }, "groundwater-wells-circle"));
  expect(onGaugeClick).toHaveBeenCalledTimes(1);
  expect(onGaugeClick).toHaveBeenCalledWith(waterData(2).gauges[0]);
  expect(onWellClick).toHaveBeenCalledTimes(1);
  expect(onWellClick).toHaveBeenCalledWith(waterData(2).wells[0]);
  mounted.unmount();
});
