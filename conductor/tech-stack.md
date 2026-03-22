# PlantGeo Technical Stack

## Frontend
| Technology | Version | Purpose |
|-----------|---------|---------|
| Next.js | 15.2+ | App Router, SSR, API routes |
| React | 19.0+ | UI framework |
| TypeScript | 5.7+ | Type safety |
| MapLibre GL JS | 5.2+ | 3D map rendering (globe, terrain, buildings) |
| deck.gl | 9.1+ | Advanced data visualization layers |
| Three.js | 0.172+ | Custom 3D objects on map |
| PMTiles | 4.2+ | Serverless tile protocol |
| react-map-gl | 7.1+ | React wrapper for MapLibre |
| Zustand | 5.0+ | Global state management |
| Jotai | 2.12+ | Per-layer atomic state |
| Tailwind CSS | 4.0+ | Utility-first styling |
| Lucide React | 0.478+ | Icons |
| CVA + clsx + tw-merge | latest | Component variants |

## Backend
| Technology | Version | Purpose |
|-----------|---------|---------|
| tRPC | 11.0+ | Type-safe API layer |
| Drizzle ORM | 0.39+ | Database ORM |
| ioredis | 5.4+ | Redis client |
| Zod | 3.24+ | Schema validation |
| postgres.js | 3.4+ | PostgreSQL driver |

## Infrastructure
| Technology | Version | Purpose |
|-----------|---------|---------|
| PostgreSQL | 16 | Primary database |
| PostGIS | 3.4 | Spatial queries and indexes |
| TimescaleDB | 2.x | Time-series hypertables |
| Martin | 1.4+ | Vector tile server (Rust) |
| Valhalla | latest | Multi-modal routing engine |
| Photon | 1.0+ | Geocoding / autocomplete |
| Redis | 7 | Caching + pub/sub |
| Nginx | alpine | Reverse proxy + tile cache |
| Docker Compose | 3.8 | Local development |

## External Services
| Service | Purpose |
|---------|---------|
| Cloudflare R2 | PMTiles basemap hosting (CDN) |
| Railway Pro | Production deployment |
| AWS Terrain Tiles | Elevation data (Terrarium encoding) |
| Geofabrik | OSM PBF data downloads |

## Testing
| Technology | Purpose |
|-----------|---------|
| Vitest | Unit/integration tests |
| Testing Library | Component tests |
| Playwright | E2E tests |
