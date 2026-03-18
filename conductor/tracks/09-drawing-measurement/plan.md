# Track 09: Drawing & Measurement - Implementation Plan

## Phase 1: Drawing Foundation
- [ ] Create drawing state machine (idle, drawing, editing)
- [ ] Implement point placement mode
- [ ] Implement polyline drawing with click-to-add-vertex
- [ ] Implement polygon drawing with snap-to-close

## Phase 2: Shape Drawing
- [ ] Add rectangle drawing mode
- [ ] Add circle drawing with radius preview
- [ ] Add freehand drawing mode
- [ ] Create DrawingToolbar component

## Phase 3: Measurement
- [ ] Implement geodesic distance calculation
- [ ] Implement area calculation
- [ ] Create MeasureTool display component
- [ ] Add unit conversion (metric/imperial)

## Phase 4: Editing
- [ ] Implement vertex editing (drag to move)
- [ ] Add vertex insert/delete
- [ ] Implement feature translate
- [ ] Build undo/redo stack

## Phase 5: Annotation
- [ ] Add text label placement
- [ ] Create arrow annotation tool
- [ ] Add color/style per annotation
- [ ] Implement annotation editing

## Phase 6: Integration
- [ ] Export drawn features as GeoJSON
- [ ] Save drawn features to PostGIS layer
- [ ] Import GeoJSON to drawing canvas
- [ ] Add elevation profile for drawn lines
