# Track 17: Places & Points of Interest

## Goal
Build a Places system equivalent to Google Places API using OpenStreetMap data, providing POI search, details, categories, and reviews.

## Features
1. **POI Search**
   - Category-based search (restaurants, gas stations, hospitals, etc.)
   - Proximity search (near me, near point)
   - Text search with fuzzy matching
   - Combined filters (category + area + rating)

2. **POI Details**
   - Name, address, phone, website
   - Opening hours
   - Category and tags
   - Photos (from Mapillary/Wikimedia)
   - User reviews/ratings (internal)

3. **POI Categories**
   - Standard OSM tag categories
   - Custom categories for domain (fire stations, water sources)
   - Category icons on map
   - Category filter chips

4. **Nearby Places**
   - "What's nearby" panel for any location
   - Distance and walking time
   - Grouped by category

## Files to Create/Modify
- `src/components/places/PlacesPanel.tsx` - POI search panel
- `src/components/places/PlaceCard.tsx` - POI detail card
- `src/components/places/CategoryFilter.tsx` - Category chips
- `src/components/places/NearbyPlaces.tsx` - Proximity list
- `src/lib/server/services/places.ts` - POI query service
- `src/lib/server/trpc/routers/places.ts` - Places tRPC router

## Acceptance Criteria
- [ ] Category search returns relevant POIs
- [ ] Proximity search works with distance display
- [ ] POI details show address, hours, contact
- [ ] Category icons render on map
- [ ] Nearby places panel shows grouped results
