# Track 05: Geocoding & Search - Implementation Plan

## Phase 1: Photon Integration
- [ ] Enhance geocoding service with geographic bias
- [ ] Create API routes for forward and reverse geocoding
- [ ] Add response formatting (Photon → standardized format)
- [ ] Handle error cases and rate limiting

## Phase 2: Search UI
- [ ] Build SearchBar with focus management
- [ ] Create SearchResults dropdown component
- [ ] Implement debounced queries (300ms)
- [ ] Add keyboard navigation (up/down/enter/escape)

## Phase 3: Reverse Geocoding
- [ ] Add right-click context menu on map
- [ ] Create ReverseGeocode popup component
- [ ] Implement long-press for mobile
- [ ] Add pin drop marker

## Phase 4: Search Features
- [ ] Add recent searches to localStorage
- [ ] Implement geographic bias based on map center
- [ ] Create result category icons (address, POI, area)
- [ ] Add map fly-to on result selection

## Phase 5: Command Palette
- [ ] Build CommandPalette with Cmd+K trigger
- [ ] Add quick actions (navigate, draw, measure)
- [ ] Integrate layer search and toggle
- [ ] Add fuzzy matching for commands

## Phase 6: Polish
- [ ] Mobile keyboard handling
- [ ] Accessibility (ARIA, focus trap)
- [ ] Loading and error states
- [ ] Tests for geocoding hooks
