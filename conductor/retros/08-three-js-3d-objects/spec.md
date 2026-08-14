# Track 08: Three.js Custom 3D Objects

## Goal
Integrate Three.js with MapLibre GL JS v5 via CustomLayerInterface to render custom 3D models (GLTF), animated objects, and procedural geometry on the map.

## Features
1. **GLTF Model Loading**
   - Load 3D models (.glb/.gltf) at map coordinates
   - Scale, rotate, position models correctly on globe/terrain
   - LOD (Level of Detail) for performance
   - Model library (trees, buildings, vehicles, sensors)

2. **Animated 3D Objects**
   - Wind turbine blades rotating
   - Vehicle models following routes
   - Pulsing alert beacons at incident locations
   - Water flow animation

3. **Procedural Geometry**
   - 3D bar charts at point locations
   - Extruded area polygons with custom height
   - Volumetric fire/smoke effects
   - Terrain-projected measurement lines

4. **Lighting & Materials**
   - Sun position-based directional lighting
   - PBR materials for realistic models
   - Emissive materials for alerts/beacons
   - Shadow casting (optional, performance-gated)

5. **Interaction**
   - Raycasting for 3D object selection
   - Info popup on 3D model click
   - Highlight/outline on hover
   - Model property inspector

## Files to Create/Modify
- `src/components/map/layers/ThreeLayer.tsx` - Three.js custom layer
- `src/components/map/layers/ModelLayer.tsx` - GLTF model placement
- `src/components/map/layers/AnimatedBeacon.tsx` - Pulsing alert
- `src/lib/map/three-utils.ts` - Matrix transforms, coordinate conversion
- `src/lib/map/model-library.ts` - Model catalog and loading
- `public/models/` - GLTF model files

## Acceptance Criteria
- [ ] GLTF models render at correct map coordinates
- [ ] Models position correctly on 3D terrain
- [ ] Models position correctly on globe view
- [ ] Animated objects (wind turbines) rotate smoothly
- [ ] Pulsing beacon shows at incident points
- [ ] Click on 3D model shows info popup
- [ ] LOD system reduces triangle count at distance
- [ ] 60fps with 50+ models visible
