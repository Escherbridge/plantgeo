# Track 05: Geocoding & Search

## Goal
Build a comprehensive search and geocoding system using Photon for autocomplete and Nominatim-backed reverse geocoding, providing a search experience comparable to Google Maps.

## Features
1. **Forward Geocoding (Search)**
   - Type-ahead autocomplete with instant results
   - Debounced queries (300ms)
   - Geographic bias (prioritize results near map center)
   - Bounding box restriction option
   - Multi-language support

2. **Reverse Geocoding**
   - Right-click → "What's here?" on map
   - Long-press on mobile
   - Returns address + POI info
   - Configurable radius

3. **Search Results UI**
   - Search bar with icon + clear button
   - Dropdown results list with address formatting
   - Result categories (address, POI, area)
   - Recent searches (localStorage)
   - Saved places (database)

4. **Map Integration**
   - Fly-to on result selection
   - Marker at search result location
   - Pin drop for reverse geocode
   - Search area highlight

5. **Command Palette**
   - Cmd/Ctrl+K to open search
   - Quick actions (navigate to, draw area, measure)
   - Layer search and toggle

## Files to Create/Modify
- `src/components/search/SearchBar.tsx` - Main search input
- `src/components/search/SearchResults.tsx` - Autocomplete dropdown
- `src/components/search/ReverseGeocode.tsx` - Right-click popup
- `src/components/search/CommandPalette.tsx` - Cmd+K palette
- `src/components/search/RecentSearches.tsx` - History list
- `src/lib/server/services/geocoding.ts` - Photon service (enhance)
- `src/app/api/geocode/route.ts` - Forward geocode API
- `src/app/api/geocode/reverse/route.ts` - Reverse geocode API
- `src/hooks/useGeocode.ts` - Geocoding React hook
- `src/hooks/useDebounce.ts` - Debounce utility hook
- `src/stores/search-store.ts` - Search state

## Acceptance Criteria
- [ ] Autocomplete returns results within 200ms
- [ ] Geographic bias prioritizes nearby results
- [ ] Right-click reverse geocoding shows address
- [ ] Cmd+K opens command palette
- [ ] Recent searches persist across sessions
- [ ] Map flies to selected search result
- [ ] Mobile search works with proper keyboard handling
- [ ] Results show formatted addresses with icons
