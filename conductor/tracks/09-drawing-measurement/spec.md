# Track 09: Drawing & Measurement Tools

## Goal
Build comprehensive drawing and measurement tools allowing users to create, edit, and annotate geographic features directly on the map - equivalent to Google Maps drawing tools.

## Features
1. **Drawing Modes**
   - Point (marker) placement
   - Line/polyline drawing
   - Polygon drawing (with snap-to-close)
   - Rectangle drawing
   - Circle drawing (radius)
   - Freehand drawing

2. **Measurement Tools**
   - Distance measurement (point-to-point, along path)
   - Area measurement (polygon area)
   - Elevation profile along drawn line
   - Radius measurement from center point
   - Unit conversion (km, miles, meters, feet, acres, hectares)

3. **Feature Editing**
   - Vertex editing (move, add, delete vertices)
   - Feature move/translate
   - Feature rotate
   - Feature scale
   - Undo/redo stack
   - Snapping to existing features

4. **Annotation**
   - Text labels on map
   - Arrow annotations
   - Color and style per annotation
   - Export annotations as GeoJSON

5. **Drawing Panel UI**
   - Drawing toolbar (floating or docked)
   - Active tool indicator
   - Measurement results display
   - Export drawn features
   - Save to layer

## Files to Create/Modify
- `src/components/tools/DrawingToolbar.tsx` - Drawing controls
- `src/components/tools/MeasureTool.tsx` - Measurement display
- `src/components/tools/VertexEditor.tsx` - Vertex editing
- `src/hooks/useDrawing.ts` - Drawing state machine
- `src/hooks/useMeasurement.ts` - Measurement calculations
- `src/lib/map/drawing.ts` - MapLibre draw interaction handlers
- `src/lib/map/measurement.ts` - Geodesic distance/area calculations
- `src/lib/geo/turf-helpers.ts` - Turf.js geometry operations
- `src/stores/drawing-store.ts` - Drawing state + undo/redo

## Acceptance Criteria
- [ ] All drawing modes work (point, line, polygon, rect, circle)
- [ ] Distance measurement shows correct geodesic distances
- [ ] Area measurement calculates correctly
- [ ] Vertex editing allows move/add/delete vertices
- [ ] Undo/redo works for all drawing operations
- [ ] Drawn features export as valid GeoJSON
- [ ] Drawn features can be saved to a PostGIS layer
- [ ] Measurement units are configurable
