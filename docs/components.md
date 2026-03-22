# Frontend Components & State Management

Complete reference for React components, custom hooks, and Zustand stores in PlantGeo.

## Component Architecture

```
src/components/
├── map/
│   ├── MapView.tsx              # Main map component
│   ├── MapControls.tsx          # Zoom, fullscreen, compass controls
│   ├── PanelManager.tsx         # Sidebar panel orchestration
│   ├── LayerManager.tsx         # Layer list and reordering
│   ├── DeckOverlay.tsx          # deck.gl layer renderer
│   ├── DeckTooltip.tsx          # Layer feature tooltips
│   ├── Legend.tsx               # Layer legend display
│   ├── TerrainControl.tsx       # Terrain exaggeration slider
│   ├── StyleSwitcher.tsx        # Basemap style selector
│   ├── GlobeToggle.tsx          # 2D/3D view toggle
│   ├── LiveIndicator.tsx        # Real-time data status
│   └── layers/                  # 30+ layer types
│       ├── ScatterLayer.tsx
│       ├── HeatmapLayer.tsx
│       ├── TripsLayer.tsx
│       ├── PathLayer.tsx
│       ├── ColumnLayer.tsx
│       ├── GeoJsonLayer.tsx
│       ├── AnimatedBeacon.tsx
│       ├── IsochroneLayer.tsx
│       ├── FireDetectionLayer.tsx (WildFire)
│       ├── WaterLayer.tsx
│       ├── WeatherLayer.tsx
│       ├── VegetationLayer.tsx
│       ├── SoilLayer.tsx
│       ├── LandCoverLayer.tsx
│       ├── CarbonPotentialLayer.tsx
│       ├── ErosionLayer.tsx
│       ├── InterventionLayer.tsx
│       ├── ReforestationLayer.tsx
│       ├── TeamLayer.tsx
│       ├── PriorityZoneLayer.tsx
│       ├── StrategyRequestLayer.tsx
│       ├── DemandHeatmapLayer.tsx
│       ├── ModelLayer.tsx
│       └── ...more layers
├── panels/
│   ├── LayerPanel.tsx           # Layer list and management
│   ├── LayerItem.tsx            # Individual layer row
│   ├── LayerFilter.tsx          # Layer filtering UI
│   ├── LayerStyler.tsx          # Style editor (colors, opacity)
│   ├── LayerUpload.tsx          # Upload custom layer data
│   ├── RoutingPanel.tsx         # Route calculator
│   ├── IsochronePanel.tsx       # Isochrone/accessibility
│   ├── SearchPanel.tsx          # Geocoding search
│   ├── TrackingPanel.tsx        # Asset tracking
│   ├── FireDashboard.tsx        # Fire alerts and stats
│   ├── WaterPanel.tsx           # Water monitoring
│   ├── SoilPanel.tsx            # Soil data viewer
│   ├── VegetationPanel.tsx      # Vegetation analysis
│   ├── AnalyticsDashboard.tsx   # User metrics
│   ├── RegionalIntelligencePanel.tsx # AI analysis
│   ├── StrategyPanel.tsx        # Strategy management
│   ├── StrategyCard.tsx         # Individual strategy
│   ├── EcosystemTracker.tsx     # Multi-layer health
│   ├── AlertPanel.tsx           # Alert list
│   ├── TeamDashboard.tsx        # Team info
│   ├── TeamProfilePanel.tsx     # Team editing
│   ├── UserPanel.tsx            # User profile
│   ├── CommunityPanel.tsx       # Community features
│   ├── ContributionQueue.tsx    # Pending contributions
│   ├── RequestSubmitModal.tsx   # Feature request form
│   └── ...more panels
├── ui/
│   ├── AlertBell.tsx            # Notification icon
│   ├── SyncIndicator.tsx        # Data sync status
│   ├── time-slider.tsx          # Time range selector
│   └── ...shared UI components
├── charts/
│   ├── LineChart.tsx            # Recharts wrapper
│   ├── BarChart.tsx
│   ├── AreaChart.tsx
│   └── ...data visualization
├── routing/
│   ├── RoutingClient.tsx        # Routing interface
│   └── RouteDisplay.tsx         # Route visualization
├── tracking/
│   ├── TrackingView.tsx         # Asset tracking display
│   └── PositionHistory.tsx      # Historical positions
├── auth/
│   ├── LoginForm.tsx            # Auth UI
│   ├── RegisterForm.tsx
│   └── ProfileDropdown.tsx
└── dashboard/
    ├── AnalyticsView.tsx        # Analytics dashboard
    └── MetricsCard.tsx          # Metric widgets
```

## Map Components

### MapView

Main map rendering component. Initializes MapLibre GL JS with deck.gl overlay.

```typescript
// src/components/map/MapView.tsx
export function MapView() {
  const mapContainer = useRef(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    // Initialize MapLibre GL JS
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: styleUrl,
      center: [lng, lat],
      zoom: zoom,
      pitch: pitch,
      bearing: bearing,
    });

    // Add deck.gl overlay
    const deckOverlay = new DeckGLOverlay({
      layers: [...],
    });
    map.addLayer(deckOverlay);

    mapRef.current = map;
  }, []);

  return (
    <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
  );
}
```

**Key Features:**
- Globe/2D toggle
- Terrain exaggeration
- Basemap style switching
- Full-screen mode
- Compass and zoom controls

**Props:** None (state from Zustand stores)

**Store Integration:** Updates map-store on pan/zoom/rotate

---

### DeckOverlay

Renders all deck.gl layers with interactive tooltips.

```typescript
export function DeckOverlay() {
  const { layers: activeLayerIds } = useLayerStore();
  const layers = activeLayerIds.map(id => createDeckLayer(id));

  return (
    <DeckGL
      layers={layers}
      controller={{ type: MapController }}
      onHover={(info) => setTooltip(info)}
      pickingRadius={5}
    />
  );
}
```

**Features:**
- Dynamic layer creation from layer store
- Interactive hover tooltips
- Click-to-identify
- Synchronized with MapLibre camera

---

### LayerManager

Displays and manages all available layers.

```typescript
export function LayerManager() {
  const { layers, toggleLayer, reorderLayers } = useLayerStore();

  return (
    <div className="layer-list">
      {layers.map(layer => (
        <LayerItem
          key={layer.id}
          layer={layer}
          onToggle={() => toggleLayer(layer.id)}
        />
      ))}
    </div>
  );
}
```

**Features:**
- Layer visibility toggle
- Reordering (drag-and-drop)
- Opacity slider per layer
- Style editor access
- Layer filtering

---

## Layer Components (30+)

Each layer component renders a specific data visualization type using deck.gl.

### ScatterLayer

Render scattered point data (e.g., fire detections, water gauges).

```typescript
export function ScatterLayer({ layerId }: Props) {
  const layer = useLayerStore(s => s.layers.find(l => l.id === layerId));
  const data = useQuery(() => trpc.layers.getLayerData.query({ id: layerId }));

  if (!data) return null;

  return new ScatterplotLayer({
    id: `scatter-${layerId}`,
    data: data.features,
    getPosition: d => [d.geometry.coordinates[0], d.geometry.coordinates[1]],
    getRadius: d => layer.style.radius || 5,
    getFillColor: d => parseColor(layer.style.color),
    getLineColor: [0, 0, 0],
    lineWidthMinPixels: 1,
    pickable: true,
  });
}
```

**Use Cases:**
- Fire detections
- Water gauge stations
- Sensor locations
- Event markers

---

### HeatmapLayer

Aggregate point data into a density heatmap.

```typescript
new HeatmapLayer({
  id: `heatmap-${layerId}`,
  data: features,
  getPosition: d => [d.geometry.coordinates[0], d.geometry.coordinates[1]],
  getWeight: d => d.properties.intensity || 1,
  radiusPixels: 100,
  intensity: 1,
  threshold: 0.05,
})
```

**Use Cases:**
- Fire intensity heatmaps
- Vegetation stress density
- Population demand maps

---

### TripsLayer

Animated vehicle movement along paths.

```typescript
new TripsLayer({
  id: `trips-${layerId}`,
  data: trips,
  getPath: d => d.path,
  getTimestamps: d => d.timestamps,
  getColor: d => [200, 30, 0],
  widthMinPixels: 2,
  trailLength: 600,
  currentTime: animationTime,
})
```

**Use Cases:**
- Vehicle route playback
- Migration patterns
- Historical movement visualization

---

### PathLayer

Render line paths (routes, boundaries).

```typescript
new PathLayer({
  id: `path-${layerId}`,
  data: paths,
  getPath: d => d.coordinates,
  getColor: d => [0, 0, 255],
  getWidth: d => d.properties.width || 5,
  widthMinPixels: 1,
})
```

**Use Cases:**
- Road networks
- Route visualization
- Boundary lines
- Stream networks

---

### ColumnLayer

3D extruded columns (bar heights represent values).

```typescript
new ColumnLayer({
  id: `column-${layerId}`,
  data: gridCells,
  getPosition: d => [d.lon, d.lat],
  getElevation: d => d.value * 1000,
  getColor: d => interpolateColor(d.value),
  elevationScale: 100,
})
```

**Use Cases:**
- Elevation/DEM visualization
- Population density columns
- Air quality/pollution 3D

---

### GeoJsonLayer

Generic GeoJSON feature rendering.

```typescript
new GeoJsonLayer({
  id: `geojson-${layerId}`,
  data: geojson,
  getFillColor: d => [200, 0, 0, 200],
  getLineColor: [0, 0, 0],
  lineWidthMinPixels: 1,
  pickable: true,
})
```

**Use Cases:**
- Custom user-uploaded layers
- Boundary polygons
- Complex feature collections

---

### IsochroneLayer

Isochrone polygons (reachability areas).

```typescript
new GeoJsonLayer({
  id: `isochrone-${layerId}`,
  data: isochrones,
  getFillColor: [0, 100, 200, 100],
  getLineColor: [0, 100, 200],
  lineWidthMinPixels: 2,
})
```

**Use Cases:**
- 15/30/60 minute walk zones
- Drive time zones
- Service area coverage

---

### AnimatedBeacon

Pulsing circle marker animation.

```typescript
export function AnimatedBeacon({ lat, lon, intensity }) {
  const [radius, setRadius] = useState(0);

  useEffect(() => {
    const anim = setInterval(() => {
      setRadius(r => (r + 10) % 500);
    }, 50);
    return () => clearInterval(anim);
  }, []);

  return new ScatterplotLayer({
    data: [{ position: [lon, lat] }],
    getRadius: () => radius,
    getFillColor: [255, 100, 0, 100 - (radius / 5)],
  });
}
```

**Use Cases:**
- Active fire locations
- Alert hotspots
- Event markers

---

## Panel Components

### LayerPanel

Sidebar panel listing all layers with controls.

```typescript
export function LayerPanel() {
  const { layers, visibility, toggleLayer } = useLayerStore();

  return (
    <div className="panel">
      <h2>Layers</h2>
      {layers.map(layer => (
        <div key={layer.id} className="layer-item">
          <input
            type="checkbox"
            checked={visibility[layer.id]}
            onChange={() => toggleLayer(layer.id)}
          />
          <label>{layer.name}</label>
          <button onClick={() => openStyler(layer.id)}>Style</button>
        </div>
      ))}
    </div>
  );
}
```

---

### RoutingPanel

Route calculation interface.

```typescript
export function RoutingPanel() {
  const { origin, destination } = useRoutingStore();
  const [route, setRoute] = useState<RouteResult | null>(null);

  const handleCalculate = async () => {
    const result = await trpc.routing.route.mutate({
      locations: [
        { lat: origin[1], lon: origin[0] },
        { lat: destination[1], lon: destination[0] },
      ],
      costing: 'auto',
    });
    setRoute(result);
  };

  return (
    <div className="panel">
      <h2>Route</h2>
      <input placeholder="From" value={originText} />
      <input placeholder="To" value={destText} />
      <button onClick={handleCalculate}>Calculate</button>
      {route && (
        <div>
          <p>Distance: {(route.routes[0].summary.length / 1000).toFixed(1)} km</p>
          <p>Time: {(route.routes[0].summary.time / 60).toFixed(0)} min</p>
        </div>
      )}
    </div>
  );
}
```

---

### FireDashboard

Real-time fire alert and statistics display.

```typescript
export function FireDashboard() {
  const { data: fires } = useQuery(
    () => trpc.wildfire.getFireDetections.query({ dayRange: 7 })
  );

  return (
    <div className="panel">
      <h2>Active Fires</h2>
      <p>{fires?.features.length || 0} detections (7-day)</p>
      <RiskGauge score={riskScore} />
      <FireList fires={fires?.features} />
    </div>
  );
}
```

---

### RegionalIntelligencePanel

AI-powered environmental analysis display.

```typescript
export function RegionalIntelligencePanel() {
  const { lat, lon } = useMapStore();
  const { data: analysis, isLoading } = useQuery(
    () => trpc.regionalIntelligence.analyzeRegion.mutate({ lat, lon })
  );

  return (
    <div className="panel">
      <h2>Regional Analysis</h2>
      {isLoading ? <Spinner /> : <div>{analysis?.analysis}</div>}
    </div>
  );
}
```

---

## Zustand Stores

Global state management located in `src/stores/`.

### useLayerStore

Manages visible layers, styles, and ordering.

```typescript
// src/stores/layer-store.ts
export const useLayerStore = create<LayerStore>((set, get) => ({
  // State
  layers: [],
  visibility: {},
  activeLayerId: null,
  filters: {},

  // Actions
  addLayer: (layer) => set((state) => ({
    layers: [...state.layers, layer],
  })),

  toggleLayer: (layerId) => set((state) => ({
    visibility: {
      ...state.visibility,
      [layerId]: !state.visibility[layerId],
    },
  })),

  setStyle: (layerId, style) => set((state) => ({
    layers: state.layers.map(l =>
      l.id === layerId ? { ...l, style } : l
    ),
  })),

  reorderLayers: (ids) => set({
    layers: ids.map(id => get().layers.find(l => l.id === id)!),
  }),
}));

// Usage in component
const { layers, toggleLayer } = useLayerStore();
```

**Key Methods:**
- `addLayer(layer)` — Add a layer
- `toggleLayer(layerId)` — Toggle visibility
- `setStyle(layerId, style)` — Update layer style
- `reorderLayers(ids)` — Reorder layers
- `removeLayer(layerId)` — Delete layer

---

### useMapStore

Map view state (center, zoom, pitch, bearing).

```typescript
export const useMapStore = create<MapStore>((set) => ({
  center: [-122.4, 37.8],
  zoom: 10,
  pitch: 0,
  bearing: 0,

  setCenter: (center) => set({ center }),
  setZoom: (zoom) => set({ zoom }),
  setPitch: (pitch) => set({ pitch }),
  setBearing: (bearing) => set({ bearing }),
  setView: (view) => set(view),
}));
```

---

### useRoutingStore

Active routing parameters and results.

```typescript
export const useRoutingStore = create<RoutingStore>((set) => ({
  origin: null,
  destination: null,
  route: null,
  isochrones: [],
  costing: 'auto',

  setOrigin: (origin) => set({ origin }),
  setDestination: (destination) => set({ destination }),
  setRoute: (route) => set({ route }),
  setCosting: (costing) => set({ costing }),
}));
```

---

### useTrackingStore

Real-time asset tracking state.

```typescript
export const useTrackingStore = create<TrackingStore>((set) => ({
  assets: [],
  positions: {},
  activeAssetId: null,
  animationTime: 0,

  addAsset: (asset) => set((state) => ({
    assets: [...state.assets, asset],
  })),

  updatePositions: (assetId, positions) => set((state) => ({
    positions: {
      ...state.positions,
      [assetId]: positions,
    },
  })),

  setActiveAsset: (assetId) => set({ activeAssetId: assetId }),
}));
```

---

### useAlertsStore

Alert notifications and dismissals.

```typescript
export const useAlertsStore = create<AlertsStore>((set) => ({
  alerts: [],
  dismissed: new Set<string>(),

  addAlert: (alert) => set((state) => ({
    alerts: [...state.alerts, alert],
  })),

  dismissAlert: (alertId) => set((state) => ({
    dismissed: new Set(state.dismissed).add(alertId),
  })),

  clearOldAlerts: () => set((state) => ({
    alerts: state.alerts.filter(
      a => Date.now() - a.createdAt.getTime() < 24 * 60 * 60 * 1000
    ),
  })),
}));
```

---

### useAuthStore

Current user and authentication state.

```typescript
export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  isLoading: true,

  setUser: (user) => set({ user }),
  logout: () => set({ user: null }),
}));
```

---

### useRegionalIntelligenceStore

AI analysis results and context.

```typescript
export const useRegionalIntelligenceStore = create<RegionalIntelStore>((set) => ({
  analysis: null,
  context: null,
  isLoading: false,

  setAnalysis: (analysis) => set({ analysis }),
  setContext: (context) => set({ context }),
  setIsLoading: (isLoading) => set({ isLoading }),
}));
```

---

## Custom Hooks

Located in `src/hooks/`.

### useSSE

Server-Sent Events hook for real-time alerts.

```typescript
// src/hooks/useSSE.ts
export function useSSE(url: string) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const eventSource = new EventSource(url);

    eventSource.onmessage = (event) => {
      try {
        setData(JSON.parse(event.data));
      } catch (e) {
        setError(e as Error);
      }
    };

    eventSource.onerror = (error) => {
      setError(error as Error);
      eventSource.close();
    };

    return () => eventSource.close();
  }, [url]);

  return { data, error };
}

// Usage
const { data: alert } = useSSE('/api/stream/alerts');
useAlertsStore.addAlert(alert);
```

---

### useWebSocket

WebSocket hook for bidirectional real-time communication.

```typescript
// src/hooks/useWebSocket.ts
export function useWebSocket(url: string) {
  const [data, setData] = useState<any>(null);
  const [status, setStatus] = useState<'connecting' | 'open' | 'closed'>('closed');
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(url);

    ws.onopen = () => setStatus('open');
    ws.onmessage = (event) => setData(JSON.parse(event.data));
    ws.onclose = () => setStatus('closed');

    wsRef.current = ws;
    return () => ws.close();
  }, [url]);

  const send = (message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  };

  return { data, status, send };
}

// Usage
const { data: position, send } = useWebSocket('wss://example.com/api/ws');
```

---

### useLiveLayer

Subscribe to and update a layer's real-time data feed.

```typescript
export function useLiveLayer(layerId: string) {
  const [data, setData] = useState<GeoJSON.FeatureCollection | null>(null);
  const { send } = useWebSocket('/api/ws');

  useEffect(() => {
    send({
      type: 'subscribe',
      channel: `layer:${layerId}`,
    });
  }, [layerId, send]);

  // Returns live data for rendering
  return data;
}
```

---

### useDeckLayers

Convert layer store to deck.gl layer instances.

```typescript
export function useDeckLayers() {
  const layers = useLayerStore(s => s.layers);

  return useMemo(() => {
    return layers
      .filter(l => l.visible)
      .map(layer => createDeckLayer(layer));
  }, [layers]);
}
```

---

### useGeocode

Geocode address to coordinates.

```typescript
export function useGeocode(query: string) {
  return useQuery(
    ['geocode', query],
    () => trpc.places.search.query({ query }),
    { enabled: query.length > 0 }
  );
}
```

---

### useRegionalIntelligence

Fetch AI analysis for a region.

```typescript
export function useRegionalIntelligence(lat: number, lon: number) {
  return useMutation(
    () => trpc.regionalIntelligence.analyzeRegion.mutate({ lat, lon }),
    {
      onSuccess: (data) => {
        useRegionalIntelligenceStore.setAnalysis(data);
      },
    }
  );
}
```

---

## Best Practices

### Component Organization

1. **One responsibility per component** — LayerItem shows a single layer, not the list
2. **Props over context** — Pass data explicitly for clarity
3. **Use "use client" sparingly** — Only on interactive components
4. **Dynamic imports for map components** — Prevents SSR hydration errors

```typescript
// Good: Dynamic import with ssr: false
const MapView = dynamic(() => import('@/components/map/MapView'), {
  ssr: false,
  loading: () => <MapSkeleton />,
});
```

### Store Organization

1. **One store per domain** — layers, routing, tracking, etc.
2. **Derived state in selectors** — Use useShallow() for partial state
3. **Immutable updates** — Spread operators or immer middleware
4. **Reset functions** — For cleanup on logout

```typescript
const useLayerStore = create<LayerStore>((set) => ({
  // ...
  reset: () => set({ layers: [], visibility: {} }),
}));
```

### Hook Patterns

1. **Compose smaller hooks** — useGeocode composed into useLocationSearch
2. **Memoize callbacks** — useCallback for event handlers passed to child components
3. **Handle errors gracefully** — Try-catch in async hooks
4. **Clean up subscriptions** — Return cleanup function from useEffect

---

## Performance Optimization

### Memoization

```typescript
const LayerItem = memo(({ layer, onToggle }: Props) => (
  // Component only re-renders if props change
  <div onClick={onToggle}>{layer.name}</div>
), (prev, next) => prev.layer.id === next.layer.id);
```

### Selector Optimization

```typescript
// ❌ Bad: Re-renders on any store change
const { layers } = useLayerStore();

// ✅ Good: Re-renders only if activeLayerId changes
const activeId = useLayerStore(s => s.activeLayerId);
```

### Lazy Loading Layers

```typescript
useEffect(() => {
  // Load layer data on demand, not upfront
  if (visibility[layerId]) {
    fetchLayerData(layerId).then(setData);
  }
}, [visibility, layerId]);
```

---

## Testing Components

```typescript
import { render, screen } from '@testing-library/react';
import { LayerPanel } from '@/components/panels/LayerPanel';
import { useLayerStore } from '@/stores/layer-store';

describe('LayerPanel', () => {
  it('toggles layer visibility', async () => {
    // Mock store
    useLayerStore.setState({
      layers: [{ id: 'fire', name: 'Fires', visible: true }],
    });

    render(<LayerPanel />);
    const checkbox = screen.getByRole('checkbox');

    fireEvent.click(checkbox);
    expect(useLayerStore.getState().visibility['fire']).toBe(false);
  });
});
```
