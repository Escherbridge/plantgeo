import { describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import type { Map as MapLibreMap, LayerSpecification } from 'maplibre-gl';
import { WeatherLayer, directionToArrow, directionToCardinal, weatherFeatures, type WeatherPoint } from '@/components/map/layers/WeatherLayer';

const sample: WeatherPoint = {
  coordinates: [-116, 44], temperature: 24, humidity: 35, windSpeed: 3, windDirection: 0,
  precipitation: 0,
  observedAt: '2026-09-10T06:45:00Z', observedDay: '2026-09-09', sampleKind: 'model_estimate',
};

function weatherMapFixture(parsed = true) {
  let styleParsed = parsed;
  let sourcesLoaded = false;
  const layers = new Map<string, LayerSpecification>();
  const sources = new Map<string, { data: GeoJSON.FeatureCollection; setData: ReturnType<typeof vi.fn> }>();
  const listeners = new Map<string, Set<() => void>>();
  const requireParsedStyle = () => { if (!styleParsed) throw new Error('Style is not done loading.'); };
  const emit = (event: string) => listeners.get(event)?.forEach((listener) => listener());
  const map = {
    getStyle: () => styleParsed ? { version: 8, sources: {}, layers: [...layers.values()] } : undefined,
    isStyleLoaded: () => styleParsed && sourcesLoaded,
    getLayer: (id: string) => layers.get(id),
    getSource: (id: string) => sources.get(id),
    addLayer: vi.fn((layer: LayerSpecification) => { requireParsedStyle(); layers.set(layer.id, layer); }),
    addSource: vi.fn((id: string, input: { data: GeoJSON.FeatureCollection }) => {
      requireParsedStyle();
      const source = { data: input.data, setData: vi.fn((data: GeoJSON.FeatureCollection) => { source.data = data; }) };
      sources.set(id, source);
    }),
    removeLayer: vi.fn((id: string) => layers.delete(id)),
    removeSource: vi.fn((id: string) => sources.delete(id)),
    setPaintProperty: vi.fn(),
    on: vi.fn((event: string, listener: () => void) => {
      const current = listeners.get(event) ?? new Set<() => void>();
      current.add(listener); listeners.set(event, current);
    }),
    off: vi.fn((event: string, listener: () => void) => listeners.get(event)?.delete(listener)),
  };
  return {
    map: map as unknown as MapLibreMap, calls: map, layers, sources, listeners, emit,
    parseStyle: () => { styleParsed = true; sourcesLoaded = false; emit('style.load'); },
    completeSources: () => { sourcesLoaded = true; emit('sourcedata'); },
    swapStyle: () => { layers.clear(); sources.clear(); styleParsed = true; sourcesLoaded = false; emit('style.load'); },
  };
}

describe('weather spatial support and wind', () => {
  it('mounts cell fills with geometry filters and scales their opacity without remounting', () => {
    const layers = new Map<string, LayerSpecification>();
    const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
    const map = {
      getLayer: (id: string) => layers.get(id), getSource: (id: string) => sources.get(id),
      addLayer: (layer: LayerSpecification) => layers.set(layer.id, layer),
      addSource: (id: string) => sources.set(id, { setData: vi.fn() }),
      removeLayer: (id: string) => layers.delete(id), removeSource: (id: string) => sources.delete(id),
      isStyleLoaded: () => true, getStyle: () => ({}), on: vi.fn(), off: vi.fn(),
      setPaintProperty: vi.fn(),
    };
    const mounted = render(<WeatherLayer map={map as unknown as MapLibreMap} data={[sample]} opacityScale={1} />);
    expect(layers.get('weather-temperature-cells')).toMatchObject({ type: 'fill', filter: ['all', ['==', ['geometry-type'], 'Polygon'], ['==', ['get', 'hasTemperature'], true]] });
    expect(layers.get('weather-temperature')).toMatchObject({ filter: ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'hasTemperature'], true], ['==', ['get', 'hasCell'], false]] });
    expect(layers.get('weather-temperature-labels')).toMatchObject({
      layout: { 'text-field': ['get', 'temperatureLabel'] },
    });
    expect(layers.get('weather-wind')).toMatchObject({
      layout: {
        'text-rotation-alignment': 'map',
        'symbol-sort-key': ['*', -1, ['get', 'windSpeed']],
        'text-allow-overlap': false,
      },
    });
    mounted.rerender(<WeatherLayer map={map as unknown as MapLibreMap} data={[sample]} opacityScale={0.5} />);
    expect(map.setPaintProperty).toHaveBeenCalledWith('weather-temperature-cells', 'fill-opacity', 0.325);
    mounted.unmount();
    expect(layers.size).toBe(0);
    expect(sources.size).toBe(0);
  });
  it('draws after a delayed mount while source tiles are pending and needs no synthetic styledata completion', () => {
    const fixture = weatherMapFixture();
    fixture.emit('style.load'); // The parsed-style event precedes the dynamic component mount.
    expect(fixture.map.isStyleLoaded()).toBe(false);
    const mounted = render(<WeatherLayer map={fixture.map} data={[sample]} />);
    expect(fixture.layers.size).toBe(4);
    const source = fixture.sources.get('weather-wind-source')!;
    expect(source.data.features[0].properties).toMatchObject({ temperature: 24 });
    const writes = source.setData.mock.calls.length;
    act(() => fixture.completeSources());
    expect(fixture.map.isStyleLoaded()).toBe(true);
    expect(fixture.calls.addSource).toHaveBeenCalledTimes(1);
    expect(fixture.calls.addLayer).toHaveBeenCalledTimes(4);
    expect(source.setData).toHaveBeenCalledTimes(writes);
    mounted.unmount();
    expect(fixture.layers.size).toBe(0);
    expect(fixture.sources.size).toBe(0);
    expect(fixture.listeners.get('style.load')?.size).toBe(0);
    act(() => fixture.emit('style.load'));
    expect(fixture.calls.addSource).toHaveBeenCalledTimes(1);
  });

  it('waits for an unparsed style and adds the latest pending props at style.load before tiles finish', () => {
    const fixture = weatherMapFixture(false);
    const mounted = render(<WeatherLayer map={fixture.map} data={[sample]} />);
    expect(fixture.calls.addSource).not.toHaveBeenCalled();
    mounted.rerender(<WeatherLayer map={fixture.map} data={[{ ...sample, temperature: 31 }]} opacityScale={0.4} />);
    act(() => fixture.completeSources());
    expect(fixture.calls.addLayer).not.toHaveBeenCalled();
    act(() => fixture.parseStyle());
    expect(fixture.map.isStyleLoaded()).toBe(false);
    expect(fixture.layers.size).toBe(4);
    expect(fixture.sources.get('weather-wind-source')?.data.features[0].properties).toMatchObject({ temperature: 31 });
    expect(fixture.layers.get('weather-temperature-cells')).toMatchObject({ paint: { 'fill-opacity': 0.26 } });
    expect(fixture.calls.on).toHaveBeenCalledTimes(1);
    mounted.unmount();
  });

  it('keeps listener order, latest props and empty sources through swaps and hidden/show transitions', () => {
    const fixture = weatherMapFixture();
    const mounted = render(<WeatherLayer map={fixture.map} data={[sample]} />);
    const original = [...fixture.listeners.get('style.load')!][0];
    const laterListener = vi.fn(); fixture.calls.on('style.load', laterListener);
    mounted.rerender(<WeatherLayer map={fixture.map} data={[{ ...sample, temperature: 31 }]} opacityScale={0.4} />);
    act(() => fixture.swapStyle());
    expect(fixture.sources.get('weather-wind-source')?.data.features[0].properties).toMatchObject({ temperature: 31 });
    expect(fixture.layers.get('weather-temperature-cells')).toMatchObject({ paint: { 'fill-opacity': 0.26 } });
    mounted.rerender(<WeatherLayer map={fixture.map} data={[]} opacityScale={0.4} />);
    expect(fixture.layers.size).toBe(4);
    expect(fixture.sources.get('weather-wind-source')?.data.features).toEqual([]);
    act(() => fixture.swapStyle());
    expect(fixture.layers.size).toBe(4);
    expect(fixture.sources.get('weather-wind-source')?.data.features).toEqual([]);
    mounted.rerender(<WeatherLayer map={fixture.map} data={[sample]} visible={false} />);
    expect(fixture.layers.size).toBe(0);
    act(() => fixture.swapStyle());
    expect(fixture.sources.size).toBe(0);
    mounted.rerender(<WeatherLayer map={fixture.map} data={[{ ...sample, temperature: 12 }]} visible opacityScale={0.5} />);
    expect(fixture.sources.get('weather-wind-source')?.data.features[0].properties).toMatchObject({ temperature: 12 });
    expect(fixture.layers.get('weather-temperature-cells')).toMatchObject({ paint: { 'fill-opacity': 0.325 } });
    expect([...fixture.listeners.get('style.load')!]).toEqual([original, laterListener]);
    expect(fixture.calls.on).toHaveBeenCalledTimes(2);
    expect(fixture.calls.off).not.toHaveBeenCalled();
    mounted.unmount();
    expect([...fixture.listeners.get('style.load')!]).toEqual([laterListener]);
    expect(fixture.layers.size).toBe(0); expect(fixture.sources.size).toBe(0);
  });

  it('cleans the previous map on replacement and only follows the current map style', () => {
    const first = weatherMapFixture(); const second = weatherMapFixture(false);
    const mounted = render(<WeatherLayer map={first.map} data={[sample]} />);
    mounted.rerender(<WeatherLayer map={second.map} data={[sample]} />);
    expect(first.layers.size).toBe(0); expect(first.sources.size).toBe(0);
    expect(first.listeners.get('style.load')?.size).toBe(0);
    act(() => first.emit('style.load')); expect(first.layers.size).toBe(0);
    expect(second.layers.size).toBe(0);
    act(() => second.parseStyle()); expect(second.layers.size).toBe(4);
    mounted.unmount();
    expect(second.layers.size).toBe(0); expect(second.sources.size).toBe(0);
  });
  it.each([[0, '↓'], [90, '←'], [180, '↑'], [270, '→'], [360, '↓']] as const)('points wind FROM %s toward %s', (direction, arrow) => {
    expect(directionToArrow(direction)).toBe(arrow);
  });
  it.each([[0, 'N'], [45, 'NE'], [180, 'S'], [270, 'W'], [360, 'N']] as const)('labels wind FROM %s with a font-safe compass value', (direction, cardinal) => {
    expect(directionToCardinal(direction)).toBe(cardinal);
  });
  it('retains unsupported detail locations as points and preserves values and clocks', () => {
    const { features } = weatherFeatures([sample]);
    expect(features).toHaveLength(1);
    expect(features[0].geometry).toEqual({ type: 'Point', coordinates: [-116, 44] });
    expect(features[0].properties).toMatchObject({ temperature: 24, temperatureLabel: '24°', precipitation: 0, windSpeed: 3, windDirection: 0, label: 'from N 3.0 m/s', observedDay: '2026-09-09', observedAt: sample.observedAt, hasCell: false, sampleKind: 'model_estimate' });
  });
  it('uses the declared aggregate footprint and puts wind at its center without changing measurements', () => {
    const { features } = weatherFeatures([{ ...sample, support: {
      zoomTier: 5, supportKind: 'aggregate_cell', supportId: 'cell', origin: 'cell_origin',
      cellOriginDegrees: [-116, 44], cellWidthDegrees: 0.2, cellHeightDegrees: 0.2,
      aggregationMethod: 'mean', contributorCount: 1,
      provenance: { sourceLayer: 'weather-observations', observedDay: '2026-09-09', newestObservedAt: sample.observedAt ?? null, attribution: 'Open-Meteo' },
    } }]);
    expect(features).toHaveLength(2);
    const polygon = features.find(feature => feature.geometry.type === 'Polygon');
    expect(polygon?.geometry.type).toBe('Polygon');
    if (polygon?.geometry.type !== 'Polygon') throw new Error('Missing weather cell');
    expect(polygon.geometry.coordinates[0][0]).toEqual([-116, 44]);
    const point = features.find(feature => feature.geometry.type === 'Point');
    if (point?.geometry.type !== 'Point') throw new Error('Missing wind anchor');
    expect(point.geometry.coordinates[0]).toBeCloseTo(-115.9);
    expect(point.geometry.coordinates[1]).toBeCloseTo(44.1);
    expect(polygon.properties).toMatchObject({ temperature: 24, windSpeed: 3, hasCell: true });
  });
  it('withholds wind glyphs when direction is missing while retaining temperature', () => {
    expect(weatherFeatures([{ ...sample, windDirection: null }]).features[0].properties).toMatchObject({ hasWind: false, hasTemperature: true, label: '' });
  });
});
