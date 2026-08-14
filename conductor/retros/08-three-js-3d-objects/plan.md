# Track 08: Three.js 3D Objects - Implementation Plan

## Phase 1: Three.js + MapLibre Integration
- [x] Create ThreeLayer implementing CustomLayerInterface
- [x] Set up shared WebGL2 context with renderingMode: '3d'
- [x] Implement getMatrixForModel coordinate transforms
- [x] Handle both Mercator and Globe projections

## Phase 2: GLTF Model Loading
- [x] Set up GLTFLoader with DRACOLoader for compression
- [x] Create ModelLayer for placing models at coordinates
- [x] Implement scale/rotation controls
- [x] Add model library catalog

## Phase 3: Animation System
- [x] Create animation loop synced with map repaint
- [x] Implement rotating wind turbines
- [x] Build pulsing beacon with emissive materials
- [x] Add vehicle models following route paths

## Phase 4: Procedural Geometry
- [x] Create 3D bar chart generator
- [x] Implement extruded polygon volumes
- [x] Add terrain-projected measurement lines

## Phase 5: Interaction
- [x] Implement raycasting for 3D object picking
- [x] Add hover highlight/outline effect
- [x] Create info popup for clicked models
- [x] Build model property inspector

## Phase 6: Performance
- [x] Implement LOD with distance-based detail levels
- [x] Add frustum culling
- [x] Optimize draw calls with instanced rendering
- [x] Memory management for model disposal
