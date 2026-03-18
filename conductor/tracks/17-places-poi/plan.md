# Track 17: Places & POI - Implementation Plan

## Phase 1: POI Data
- [ ] Import OSM POI data via osm2pgsql
- [ ] Create POI table with categories
- [ ] Build PostGIS proximity queries
- [ ] Index POI names with pg_trgm

## Phase 2: Search
- [ ] Create category search endpoint
- [ ] Add proximity search (near point)
- [ ] Implement text fuzzy search
- [ ] Build combined filter queries

## Phase 3: UI
- [ ] Build PlacesPanel with search
- [ ] Create PlaceCard detail view
- [ ] Add CategoryFilter chips
- [ ] Build NearbyPlaces panel

## Phase 4: Map Integration
- [ ] Add POI marker layer on map
- [ ] Category icon sprites
- [ ] Cluster POIs at low zoom
- [ ] Click marker → show PlaceCard
