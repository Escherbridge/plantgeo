import { describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import type { Map as MapLibreMap, LayerSpecification } from 'maplibre-gl';
import { WeatherLayer, directionToArrow, directionToCardinal, weatherFeatures, type WeatherPoint } from '@/components/map/layers/WeatherLayer';

const sample: WeatherPoint = {
  coordinates: [-116, 44], temperature: 24, humidity: 35, windSpeed: 3, windDirection: 0,
  precipitation: 0,
  observedAt: '2026-09-10T06:45:00Z', observedDay: '2026-09-09', sampleKind: 'model_estimate',
};

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
  it('retries a missed style-load event when the current style becomes ready', async () => {
    const layers = new Map<string, LayerSpecification>();
    const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
    const listeners = new Map<string, Set<() => void>>();
    let styleLoaded = false;
    const map = {
      getLayer: (id: string) => layers.get(id), getSource: (id: string) => sources.get(id),
      addLayer: (layer: LayerSpecification) => layers.set(layer.id, layer),
      addSource: (id: string) => sources.set(id, { setData: vi.fn() }),
      removeLayer: (id: string) => layers.delete(id), removeSource: (id: string) => sources.delete(id),
      isStyleLoaded: () => styleLoaded, getStyle: () => ({}),
      on: (type: string, listener: () => void) => {
        const subscribers = listeners.get(type) ?? new Set<() => void>();
        subscribers.add(listener);
        listeners.set(type, subscribers);
      },
      off: (type: string, listener: () => void) => listeners.get(type)?.delete(listener),
      setPaintProperty: vi.fn(),
    };
    const mounted = render(<WeatherLayer map={map as unknown as MapLibreMap} data={[sample]} />);
    expect(layers.size).toBe(0);

    styleLoaded = true;
    act(() => listeners.get('styledata')?.forEach(listener => listener()));

    await waitFor(() => expect(layers.size).toBe(4));
    expect(sources.size).toBe(1);
    expect([...layers.keys()]).toEqual(expect.arrayContaining([
      'weather-temperature-cells',
      'weather-temperature',
      'weather-temperature-labels',
      'weather-wind',
    ]));
    mounted.unmount();
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
