# PlantGeo - Conductor Tracks

## Track Overview

20 tracks covering full Google Maps Platform feature parity with open-source technologies.

### Foundation Layer (Build First)
| # | Track | Priority | Dependencies |
|---|-------|----------|-------------|
| 01 | [Core Map Engine](./01-core-map-engine/) | P0 | None |
| 02 | [Vector Tile Pipeline](./02-vector-tile-pipeline/) | P0 | Track 01 |
| 15 | [UI Design System](./15-ui-design-system/) | P0 | None |

### Core Features (Build Second)
| # | Track | Priority | Dependencies |
|---|-------|----------|-------------|
| 03 | [deck.gl Visualization](./03-deck-gl-visualization/) | P0 | Track 01 |
| 04 | [Routing & Navigation](./04-routing-navigation/) | P0 | Track 01, 05 |
| 05 | [Geocoding & Search](./05-geocoding-search/) | P0 | Track 15 |
| 06 | [Real-Time Streaming](./06-realtime-data-streaming/) | P0 | Track 01, 02 |
| 07 | [Layer Management](./07-layer-management/) | P0 | Track 01, 02, 15 |

### Advanced Features (Build Third)
| # | Track | Priority | Dependencies |
|---|-------|----------|-------------|
| 08 | [Three.js 3D Objects](./08-three-js-3d-objects/) | P1 | Track 01 |
| 09 | [Drawing & Measurement](./09-drawing-measurement/) | P1 | Track 01, 07 |
| 10 | [Wildfire Prevention](./10-wildfire-prevention/) | P1 | Track 01, 03, 06 |
| 13 | [Analytics Dashboard](./13-analytics-dashboard/) | P1 | Track 07, 06 |
| 14 | [Fleet Tracking](./14-fleet-tracking/) | P1 | Track 06, 01 |

### Enterprise Features (Build Fourth)
| # | Track | Priority | Dependencies |
|---|-------|----------|-------------|
| 12 | [Auth & Multi-Tenancy](./12-auth-multitenancy/) | P1 | Track 07 |
| 16 | [Street-Level Imagery](./16-street-view-imagery/) | P2 | Track 01 |
| 17 | [Places & POI](./17-places-poi/) | P2 | Track 02, 05 |
| 20 | [Embed & API](./20-embed-api/) | P2 | Track 04, 05, 07 |

### Platform (Build Throughout)
| # | Track | Priority | Dependencies |
|---|-------|----------|-------------|
| 11 | [Offline & PWA](./11-offline-pwa/) | P2 | Track 01, 02, 07 |
| 18 | [Railway Deployment](./18-railway-deployment/) | P1 | All tracks |
| 19 | [Testing & Quality](./19-testing-quality/) | P0 | All tracks |

## Recommended Build Order

### Sprint 1: Foundation
- Track 15 (UI Design System) + Track 01 (Core Map Engine)

### Sprint 2: Data Infrastructure
- Track 02 (Vector Tiles) + Track 05 (Geocoding)

### Sprint 3: Core Interactivity
- Track 07 (Layer Management) + Track 04 (Routing)
- Track 03 (deck.gl) in parallel

### Sprint 4: Real-Time & 3D
- Track 06 (Real-Time) + Track 08 (Three.js)
- Track 09 (Drawing) in parallel

### Sprint 5: Domain Features
- Track 10 (Wildfire) + Track 14 (Fleet)
- Track 13 (Analytics) in parallel

### Sprint 6: Enterprise
- Track 12 (Auth) + Track 18 (Railway Deploy)
- Track 19 (Testing) throughout

### Sprint 7: Platform
- Track 16 (Street View) + Track 17 (Places)
- Track 20 (Embed/API) + Track 11 (PWA)

## Google Maps Feature Parity Matrix

| Google Maps Feature | PlantGeo Track | Open-Source Tech |
|---|---|---|
| Maps SDK | Track 01 | MapLibre GL JS v5 |
| 3D Buildings | Track 01 | fill-extrusion layer |
| Street View | Track 16 | Mapillary |
| Directions API | Track 04 | Valhalla |
| Distance Matrix API | Track 04 | Valhalla matrix |
| Geocoding API | Track 05 | Photon |
| Places API | Track 17 | PostGIS + OSM |
| Elevation API | Track 01 | AWS Terrain Tiles |
| Drawing Tools | Track 09 | Custom MapLibre |
| Heatmaps | Track 03 | deck.gl HeatmapLayer |
| Map Tiles | Track 02 | Martin + PMTiles |
| Real-Time | Track 06 | SSE + WebSocket |
| Embed API | Track 20 | Custom iframe |
| Analytics | Track 13 | PostGIS + Recharts |
