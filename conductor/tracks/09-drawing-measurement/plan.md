# Track 09: Drawing & Measurement - Implementation Plan

## Phase 1: Drawing Foundation
- [x] Create drawing state machine (idle, drawing, editing)
- [x] Implement point placement mode
- [x] Implement polyline drawing with click-to-add-vertex
- [x] Implement polygon drawing with snap-to-close

## Phase 2: Shape Drawing
- [x] Add rectangle drawing mode
- [x] Add circle drawing with radius preview
- [x] Add freehand drawing mode
- [x] Create DrawingToolbar component

## Phase 3: Measurement
- [x] Implement geodesic distance calculation
- [x] Implement area calculation
- [x] Create MeasureTool display component
- [x] Add unit conversion (metric/imperial)

## Phase 4: Editing
- [x] Implement vertex editing (drag to move)
- [x] Add vertex insert/delete
- [x] Implement feature translate
- [x] Build undo/redo stack

## Phase 5: Annotation
- [x] Add text label placement
- [x] Create arrow annotation tool
- [x] Add color/style per annotation
- [x] Implement annotation editing

## Phase 6: Integration
- [x] Export drawn features as GeoJSON
- [x] Save drawn features to PostGIS layer
- [x] Import GeoJSON to drawing canvas
- [x] Add elevation profile for drawn lines
