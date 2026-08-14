# Track 01: Core Map Engine

## Goal
Build the foundational 3D map rendering engine with MapLibre GL JS v5, including globe view, 3D terrain, sky/atmosphere, and base tile sources.

## Features
1. **MapLibre GL JS v5 Integration**
   - Initialize map with WebGL2 rendering
   - PMTiles protocol registration for serverless basemaps
   - Dynamic import pattern (no SSR) for Next.js compatibility

2. **3D Terrain**
   - raster-dem source from AWS Terrain Tiles (Terrarium encoding)
   - Terrain exaggeration controls (0.5x to 3x slider)
   - Hillshade layer for depth perception

3. **Globe View**
   - Toggle between Mercator and Globe projections
   - Atmosphere/sky rendering with zoom-based blend
   - Smooth projection transition animation

4. **3D Buildings**
   - fill-extrusion layer from OpenMapTiles building data
   - Data-driven height from `render_height` property
   - Color interpolation based on building height
   - Toggle visibility at zoom 15+

5. **Sky & Atmosphere**
   - Day/night sky color schemes
   - Fog for depth perception
   - Atmosphere blend on globe view

6. **Base Map Styles**
   - Dark mode (default for geospatial analysis)
   - Light mode
   - Satellite imagery option
   - Style switcher UI control

7. **Map Controls**
   - Navigation (zoom, rotate, pitch)
   - Scale bar
   - Geolocate (GPS)
   - Fullscreen
   - Compass bearing indicator

## Files to Create/Modify
- `src/components/map/MapView.tsx` - Core map component (enhance existing)
- `src/components/map/MapControls.tsx` - Custom control panel
- `src/components/map/StyleSwitcher.tsx` - Base map style selector
- `src/components/map/TerrainControl.tsx` - 3D terrain toggle + exaggeration
- `src/components/map/GlobeToggle.tsx` - Globe/Mercator switch
- `src/lib/map/styles.ts` - Map style definitions (dark, light, satellite)
- `src/lib/map/terrain.ts` - Terrain source configuration
- `src/lib/map/buildings.ts` - 3D building layer config
- `src/stores/map-store.ts` - Map state (enhance existing)

## Acceptance Criteria
- [ ] Map renders with 3D terrain and hillshade
- [ ] Globe view toggles smoothly with atmosphere
- [ ] 3D buildings appear at zoom 15+ with height-based coloring
- [ ] Style switcher works between dark/light/satellite
- [ ] Terrain exaggeration slider controls elevation scale
- [ ] All controls work on mobile (touch gestures)
- [ ] Performance: 60fps on mid-range hardware
