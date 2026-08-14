# Track 16: Street-Level Imagery

## Goal
Integrate open-source street-level imagery from Mapillary and KartaView as an alternative to Google Street View, providing panoramic and sequential image browsing.

## Features
1. **Mapillary Integration**
   - Coverage layer showing available imagery
   - Panoramic image viewer
   - Image sequences (drive-along)
   - Point cloud visualization
   - Object detection overlays

2. **Image Viewer**
   - 360-degree panoramic view
   - Navigate between images (arrows)
   - Map-image sync (click on map → show image)
   - Split view (map + imagery)
   - Mini-map in panorama view

3. **Street Coverage**
   - Coverage heatmap by date
   - Filter by date range
   - Filter by image quality
   - Contributor attribution

## Files to Create/Modify
- `src/components/imagery/PanoViewer.tsx` - 360 panorama viewer
- `src/components/imagery/StreetCoverage.tsx` - Coverage layer
- `src/components/imagery/ImageNav.tsx` - Sequence navigation
- `src/lib/server/services/mapillary.ts` - Mapillary API
- `src/components/map/layers/ImageryLayer.tsx` - Coverage overlay

## Acceptance Criteria
- [ ] Coverage layer shows available street imagery
- [ ] Panoramic viewer renders 360-degree images
- [ ] Navigate between sequential images
- [ ] Map-image sync works bidirectionally
- [ ] Split view with map + panorama
