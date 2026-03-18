# PlantGeo Implementation Playbook

> Optimized prompts for oh-my-claudecode plugins. Code-first, no commits.
> Run type-check + tests only at the end of each sprint.
> Developer reviews and commits manually afterward.

## Plugin Strategy

| Plugin | When to Use |
|--------|------------|
| `/ultrapilot` | Primary workhorse — parallel file ownership across 3-5 agents |
| `/ultrawork` | Fallback when ultrapilot hits issues — similar parallel throughput |
| `/swarm` | Best for tracks with many small independent tasks (UI components) |
| `/build-fix` | Run after each sprint to clean up TypeScript errors |
| `/ecomode` | Token-efficient for simpler tracks (saves cost, uses Haiku/Sonnet) |
| `/pipeline` | Sequential chains where output feeds next stage (e.g., schema → service → API → UI) |

## Rules for All Prompts

Add this suffix to EVERY prompt to enforce the workflow:

```
RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Do NOT add error handling beyond what the spec requires.
- Prefer editing existing files over creating new ones when possible.
- Use `"use client"` only on components that need browser APIs (map, interactive UI).
- Dynamic import MapLibre/deck.gl components with `ssr: false`.
- Follow the existing patterns in the codebase (Zustand stores, Drizzle schema, tRPC routers).
```

---

## SPRINT 1: Foundation (Tracks 15 + 01)

### Prompt 1A — UI Design System (Track 15)

**Plugin:** `/ultrapilot`

```
Implement the PlantGeo UI Design System (Track 15) — a dark-mode-first component library with emerald accent (#10b981).

Read the full spec at conductor/tracks/15-ui-design-system/spec.md and plan at conductor/tracks/15-ui-design-system/plan.md.

EXISTING CODE:
- src/styles/globals.css — has Tailwind v4 import and CSS variables already
- src/lib/providers.tsx — exists
- package.json — has tailwindcss v4, class-variance-authority, clsx, tailwind-merge, lucide-react

IMPLEMENT ALL 6 PHASES:

Phase 1 — Foundation:
- Create src/lib/utils.ts with cn() utility (clsx + tailwind-merge)
- Extend globals.css with full design token CSS variables: colors (slate-900 bg, emerald accent), spacing, radius, shadows, glassmorphic backdrop-filter values
- Dark mode as default, light mode via .light class on <html>

Phase 2 — Core Components (all in src/components/ui/):
- button.tsx — variants: default, destructive, outline, secondary, ghost, link; sizes: sm, default, lg, icon. Use CVA.
- input.tsx — dark-themed input with focus ring
- select.tsx — styled select with dark background
- slider.tsx — range slider (for terrain exaggeration, opacity, etc.)
- dialog.tsx — modal with backdrop blur overlay
- sheet.tsx — slide-in panel from left/right/bottom

Phase 3 — Navigation Components:
- tabs.tsx — tab navigation with active indicator
- dropdown-menu.tsx — context menu with keyboard nav
- command.tsx — command palette (Cmd+K) using cmdk pattern
- popover.tsx — positioned popover
- tooltip.tsx — hover tooltip

Phase 4 — Map Layout:
- src/components/layout/MapLayout.tsx — flex container: optional SidePanel + map area + optional BottomSheet
- src/components/layout/SidePanel.tsx — resizable side panel (300-600px), collapse button, glassmorphic bg
- src/components/layout/BottomSheet.tsx — mobile bottom sheet with drag handle, snap points (25%, 50%, 90%)

Phase 5 — Map-Specific Components:
- src/components/ui/floating-toolbar.tsx — horizontal toolbar that floats over map, glassmorphic
- src/components/ui/map-popup.tsx — styled popup for map features
- src/components/ui/coordinate-display.tsx — shows lat/lng/zoom at bottom of map
- src/components/ui/zoom-indicator.tsx — current zoom level badge

Phase 6 — Feedback Components:
- src/components/ui/toast.tsx — toast notification system (success, error, warning, info)
- src/components/ui/skeleton.tsx — loading skeleton components
- src/components/ui/loading-overlay.tsx — fullscreen loading with spinner
- src/components/ui/theme-toggle.tsx — dark/light mode switch

Update src/app/page.tsx to use MapLayout wrapping MapView.
Update src/app/layout.tsx to include the Toaster provider.

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Do NOT add error handling beyond what the spec requires.
- Prefer editing existing files over creating new ones when possible.
- Use `"use client"` only on components that need browser APIs (map, interactive UI).
- Follow the existing patterns in the codebase.
```

### Prompt 1B — Core Map Engine (Track 01)

**Plugin:** `/ultrapilot`

> **Run AFTER Prompt 1A completes** (Track 01 Phase 5 uses UI components from Track 15)

```
Implement the Core Map Engine (Track 01) — MapLibre GL JS v5 with 3D terrain, globe view, 3D buildings, and controls.

Read the full spec at conductor/tracks/01-core-map-engine/spec.md and plan at conductor/tracks/01-core-map-engine/plan.md.

EXISTING CODE:
- src/components/map/MapView.tsx — basic MapLibre init with PMTiles protocol, terrain-dem, hillshade, OSM raster tiles, sky rendering. Has NavigationControl, ScaleControl, GeolocateControl, FullscreenControl.
- src/stores/map-store.ts — Zustand store with viewport, is3DEnabled, isGlobeView, terrainExaggeration, activeLayers, selectedFeatureId.
- src/components/ui/ — full design system components from Track 15 (button, slider, dialog, sheet, tabs, etc.)
- src/components/layout/ — MapLayout, SidePanel, BottomSheet

IMPLEMENT ALL 6 PHASES:

Phase 1 — Map Foundation:
- Create src/lib/map/styles.ts with 3 full StyleSpecification objects: dark (dark-matter vector style), light (positron vector style), satellite (raster satellite). Each must include terrain-dem source, vector tile sources for buildings. Use Protomaps PMTiles free tiles (pmtiles:// protocol) for dark and light styles.
- Enhance MapView.tsx: accept style prop from store, handle style switching without re-mounting, proper WebGL context loss recovery (webglcontextlost/restored events), expose map instance via ref or context.
- Add currentStyle to map-store.ts (dark | light | satellite)

Phase 2 — 3D Terrain:
- Ensure terrain-dem source is in all styles (AWS Terrain Tiles terrarium encoding)
- Create src/components/map/TerrainControl.tsx — slider (0-3x) using the UI slider component, toggle terrain on/off, positioned as floating control
- Wire terrain exaggeration to map.setTerrain() reactively when store changes
- Hillshade layer already exists — ensure it's in all style specs

Phase 3 — Globe View:
- Create src/components/map/GlobeToggle.tsx — toggle button using UI button component
- Implement globe/mercator projection toggle via map.setProjection()
- Add atmosphere blend: at low zoom show atmosphere, at high zoom fade to flat
- Smooth animation on projection change (flyTo with zoom adjustment)

Phase 4 — 3D Buildings:
- Add building vector source to styles (OpenMapTiles or Protomaps buildings)
- Create fill-extrusion layer config in styles.ts: height from feature properties, base from min_height
- Zoom-based opacity: invisible below z14, fade in z14-z16, full at z16+
- Color buildings by height gradient (short=slate, tall=emerald accent)

Phase 5 — Sky & Controls:
- Implement setSky per style: dark theme (navy/purple), light theme (blue/white), satellite (blue/cyan)
- Create src/components/map/StyleSwitcher.tsx — grid of style preview thumbnails using UI components, updates currentStyle in store
- Create src/components/map/MapControls.tsx — unified floating panel combining: StyleSwitcher, TerrainControl, GlobeToggle, 3D toggle. Use the floating-toolbar UI component.
- Add keyboard shortcuts: R=reset view, T=toggle terrain, G=toggle globe, 1/2/3=style switch

Phase 6 — Polish:
- Mobile pinch-to-zoom and two-finger-rotate should work (MapLibre default, verify)
- Add loading state in MapView (show skeleton while style loads)
- Create error boundary wrapper for WebGL failures
- Export map instance via React context (src/lib/map/map-context.ts) so other components can access it

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Do NOT add error handling beyond what the spec requires.
- Prefer editing existing files over creating new ones when possible.
- Use `"use client"` only on components that need browser APIs (map, interactive UI).
- Dynamic import MapLibre components with `ssr: false`.
- Follow the existing patterns in the codebase (Zustand stores, etc.).
```

### Post-Sprint 1 — Build Fix

**Plugin:** `/build-fix`

```
Fix all TypeScript and build errors in the codebase after implementing Track 15 (UI Design System) and Track 01 (Core Map Engine). Run `npx tsc --noEmit` to find errors, then fix them with minimal changes. Do not change any business logic or architecture — only fix types, imports, and missing dependencies.
```

---

## SPRINT 2: Data Infrastructure (Tracks 02 + 05)

### Prompt 2A — Geocoding & Search (Track 05)

**Plugin:** `/ultrapilot`

> Can run in parallel with 2B since Track 05 depends on Track 15 (done) and Track 02 is independent.

```
Implement Geocoding & Search (Track 05) — Photon autocomplete, reverse geocoding, command palette.

Read conductor/tracks/05-geocoding-search/spec.md and conductor/tracks/05-geocoding-search/plan.md.

EXISTING CODE:
- src/lib/server/services/geocoding.ts — forwardGeocode() and reverseGeocode() wrappers for Photon API already exist
- src/components/ui/ — full design system (command.tsx for Cmd+K, input, popover, dialog, etc.)
- src/components/layout/ — MapLayout with SidePanel
- src/stores/map-store.ts — has viewport with longitude/latitude for geographic bias

IMPLEMENT ALL 6 PHASES:

Phase 1 — Photon Integration Enhancement:
- Enhance geocoding.ts: add Zod validation on responses, add geographic bias (pass map center lat/lon automatically), format addresses into display strings, add result type normalization (house, street, city, country)
- Create src/app/api/geocode/route.ts — Next.js API route proxying forwardGeocode with query params
- Create src/app/api/geocode/reverse/route.ts — reverse geocode API route

Phase 2 — Search UI:
- Create src/hooks/useDebounce.ts — generic debounce hook (300ms default)
- Create src/hooks/useGeocode.ts — hook wrapping forward geocode with debounce, loading state, error state
- Create src/components/search/SearchBar.tsx — input field with search icon, clear button, positioned in top-left of map. Uses floating-toolbar styling.
- Create src/components/search/SearchResults.tsx — dropdown list below SearchBar showing results with icons by type, keyboard navigation (up/down/enter/esc)
- Create src/stores/search-store.ts — Zustand: query, results, selectedIndex, isOpen, recentSearches

Phase 3 — Reverse Geocoding:
- Create src/components/search/ReverseGeocode.tsx — popup that appears on map right-click, shows address at click point, "copy coordinates" and "directions from/to here" actions
- Register contextmenu event on map to trigger reverse geocode
- Mobile: long-press triggers same behavior

Phase 4 — Search Features:
- Create src/components/search/RecentSearches.tsx — list of recent searches from localStorage (max 10)
- When a result is selected: fly map to coordinates, place a temporary marker, close dropdown
- Category icons: house, road, city, country (use lucide-react icons)
- Store recent searches in search-store persisted to localStorage

Phase 5 — Command Palette:
- Create src/components/search/CommandPalette.tsx — Cmd+K modal using the command.tsx UI component
- Actions: search places, toggle layers, switch style, toggle terrain, toggle globe, go to coordinates
- Fuzzy matching on action names
- Wire keyboard shortcut globally

Phase 6 — Polish:
- Mobile: SearchBar collapses to icon, expands on tap
- Loading spinner in SearchResults during fetch
- Empty state and error state in SearchResults
- Fly-to animation uses map.flyTo() with appropriate zoom based on result type

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Do NOT add error handling beyond what the spec requires.
- Use `"use client"` only on components that need browser APIs.
- Follow existing patterns (Zustand stores, Next.js API routes).
```

### Prompt 2B — Vector Tile Pipeline (Track 02)

**Plugin:** `/ecomode`

> Mostly infra config files, not heavy TypeScript — ecomode is cost-efficient here.

```
Implement the Vector Tile Pipeline (Track 02) — Martin tile server config, PostGIS tile functions, layer/source definitions.

Read conductor/tracks/02-vector-tile-pipeline/spec.md and conductor/tracks/02-vector-tile-pipeline/plan.md.

EXISTING CODE:
- infra/martin/martin.yaml — exists (may need enhancement)
- infra/db/init/01-extensions.sql — exists
- infra/nginx/nginx.conf — exists
- docker-compose.yml — Martin, PostGIS, Nginx services configured
- src/components/map/MapView.tsx — uses PMTiles protocol, has buildStyle()
- src/lib/map/styles.ts — created in Sprint 1 with dark/light/satellite styles

IMPLEMENT:

Phase 1 — Martin Configuration:
- Enhance infra/martin/martin.yaml: configure auto-discovery from PostGIS, add PMTiles sources path (/pmtiles), MBTiles path (/mbtiles), sprites path (/sprites), fonts/glyphs path (/fonts)
- Ensure connection to PostGIS geo schema

Phase 2 — PostGIS Tile Functions:
- Create infra/db/init/02-tile-functions.sql with:
  - fire_risk_tiles(z, x, y) — returns MVT from geo.features where layer is fire-risk
  - sensor_tiles(z, x, y) — returns MVT from sensor locations
  - intervention_tiles(z, x, y) — returns MVT from intervention zones
  - building_tiles(z, x, y) — returns MVT from building footprints
  - Each function uses ST_AsMVT, ST_AsMVTGeom, ST_TileEnvelope

Phase 3 — OSM Data Import:
- Create scripts/import-osm.sh — downloads California PBF, runs osm2pgsql with flex output
- Create infra/db/import/osm-flex-config.lua — flex config that imports: buildings (with height), roads, waterways, landuse, POIs into geo schema tables
- Create infra/db/init/03-osm-tables.sql — table definitions for imported OSM data with geometry columns and spatial indexes

Phase 4 — PMTiles Setup:
- Create scripts/setup-pmtiles.sh — downloads Protomaps planet PMTiles (or region extract), uploads to Cloudflare R2 bucket via rclone/aws cli
- Document R2 bucket CORS configuration

Phase 5 — Layer Definitions:
- Create src/lib/map/sources.ts — typed MapLibre source definitions: martin-dynamic (vector from Martin), pmtiles-basemap, terrain-dem, satellite-raster. Export a getSources(martinUrl, pmtilesUrl) function.
- Create src/lib/map/layers.ts — typed MapLibre layer definitions for: buildings-3d, fire-perimeters, sensors, interventions, roads, waterways. Export a getLayers() function.
- Update styles.ts to use sources.ts and layers.ts instead of inline definitions

Phase 6 — Caching:
- Enhance infra/nginx/nginx.conf: add proxy_cache for Martin tile endpoints, set Cache-Control headers (basemap tiles: 1 week, dynamic tiles: 5 min), gzip MVT responses
- Add tile cache warming note in setup-pmtiles.sh

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Follow the existing patterns in the codebase.
- SQL files should be idempotent (CREATE OR REPLACE, IF NOT EXISTS).
- Shell scripts should have proper error handling (set -euo pipefail).
```

### Post-Sprint 2 — Build Fix

**Plugin:** `/build-fix`

```
Fix all TypeScript and build errors after implementing Track 05 (Geocoding & Search) and Track 02 (Vector Tile Pipeline). Run `npx tsc --noEmit`, fix type errors with minimal changes. Do not alter business logic.
```

---

## SPRINT 3: Core Interactivity (Tracks 07 + 04 + 03)

### Prompt 3A — tRPC Setup + Layer Management (Track 07)

**Plugin:** `/ultrapilot`

```
Implement the Layer Management System (Track 07) — tRPC setup, layer CRUD, panel UI, styling, upload, filtering, legend.

Read conductor/tracks/07-layer-management/spec.md and conductor/tracks/07-layer-management/plan.md.

EXISTING CODE:
- package.json — has @trpc/server, @trpc/client, @trpc/react-query, @tanstack/react-query, superjson, zod
- src/lib/server/db/schema.ts — has geo.layers and geo.features tables
- src/lib/server/db/index.ts — Drizzle client
- src/lib/providers.tsx — React providers wrapper
- src/components/ui/ — full design system (sheet, dialog, tabs, button, input, slider, dropdown-menu, popover, toast, etc.)
- src/components/layout/ — MapLayout with SidePanel
- src/stores/map-store.ts — has activeLayers[], toggleLayer()
- src/lib/map/layers.ts — layer definitions from Track 02
- src/lib/map/sources.ts — source definitions from Track 02

IMPLEMENT ALL 6 PHASES — THIS IS CRITICAL as it sets up tRPC for the entire app:

Phase 1 — tRPC Foundation + Layer CRUD:
- Create src/lib/server/trpc/init.ts — tRPC initialization with superjson transformer, context with db
- Create src/lib/server/trpc/router.ts — app router that merges sub-routers
- Create src/lib/server/trpc/routers/layers.ts — layer router with: list, getById, create, update, delete, reorder procedures. Zod input validation.
- Create src/app/api/trpc/[trpc]/route.ts — Next.js API handler
- Create src/lib/trpc/client.ts — tRPC React client setup with react-query
- Update src/lib/providers.tsx — add tRPC + QueryClient providers

Phase 2 — Layer Panel UI:
- Create src/components/panels/LayerPanel.tsx — sidebar panel showing all layers, toggle visibility, opacity slider per layer
- Create src/components/panels/LayerItem.tsx — individual layer row: icon, name, visibility toggle, opacity, expand arrow, drag handle
- Implement drag-to-reorder (use native HTML drag and drop, or a lightweight approach — no new deps)
- Wire to tRPC layer.list query and map-store activeLayers

Phase 3 — Layer Styling:
- Create src/components/panels/LayerStyler.tsx — dialog/sheet for editing layer style: fill color, stroke color, stroke width, opacity, radius (for points). Use color input and the UI slider.
- Create src/lib/map/layer-styles.ts — preset style objects (fire-red, eco-green, water-blue, neutral-gray) and a function to convert LayerStyler output to MapLibre paint properties
- Apply style changes to map in real-time via map.setPaintProperty()

Phase 4 — Data Upload:
- Create src/components/panels/LayerUpload.tsx — drag-and-drop zone that accepts .geojson, .json, .csv, .kml files
- Parse GeoJSON directly, CSV with lat/lon columns → Point features, KML → GeoJSON conversion
- On upload: create new layer via tRPC, add features, add source+layer to map
- Create src/lib/server/services/layers.ts — service functions for bulk feature insert to PostGIS

Phase 5 — Filtering:
- Create src/components/panels/LayerFilter.tsx — filter builder: property name dropdown, operator (=, !=, >, <, contains), value input
- Convert filters to MapLibre filter expressions and apply via map.setFilter()
- Support combining multiple filters with AND/OR

Phase 6 — Legend:
- Create src/components/map/Legend.tsx — floating panel showing active layer legends: color swatches, graduated symbols, labels
- Auto-generate legend items from layer style config
- Toggle legend visibility
- Create src/stores/layer-store.ts — Zustand store for layer UI state: selectedLayerId, filterExpressions, styleOverrides, legendVisible

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Use `"use client"` only on components that need browser APIs.
- Dynamic import anything using MapLibre with `ssr: false`.
- Follow existing patterns (Zustand, Drizzle ORM).
```

### Prompt 3B — Routing & Navigation (Track 04)

**Plugin:** `/ultrapilot`

> Can run in parallel with 3A.

```
Implement Routing & Navigation (Track 04) — Valhalla routing, isochrones, route visualization, directions panel.

Read conductor/tracks/04-routing-navigation/spec.md and conductor/tracks/04-routing-navigation/plan.md.

EXISTING CODE:
- src/lib/server/services/routing.ts — getRoute(), getIsochrone(), getMatrix() wrappers for Valhalla already exist
- src/hooks/useGeocode.ts — geocoding hook from Track 05
- src/components/search/SearchBar.tsx — can be reused as WaypointInput pattern
- src/components/ui/ — full design system
- src/lib/map/map-context.ts — map instance context from Track 01
- src/lib/server/trpc/ — tRPC setup from Track 07

IMPLEMENT ALL 6 PHASES:

Phase 1 — Valhalla Integration Enhancement:
- Enhance src/lib/server/services/routing.ts: add Zod schemas for request/response validation, add transit costing option, decode Valhalla encoded polyline to GeoJSON LineString, extract maneuver list with instruction text
- Create src/lib/server/trpc/routers/routing.ts — tRPC router with: route, isochrone, matrix procedures
- Create src/app/api/route/route.ts — Next.js API route (if not using tRPC for this)
- Create src/app/api/isochrone/route.ts
- Create src/stores/routing-store.ts — Zustand: origin, destination, waypoints[], activeRoute, alternatives[], transportMode, isCalculating

Phase 2 — Route Visualization:
- Create src/components/map/layers/RouteLayer.tsx — renders route GeoJSON as a styled line on map: primary route (emerald, 5px), alternatives (gray, 3px dashed)
- Animated dashes flowing along route direction (line-dasharray animation)
- Turn markers at maneuver points (small circles with direction icons)
- Auto fit-to-bounds when route calculated

Phase 3 — Routing Panel UI:
- Create src/components/panels/RoutingPanel.tsx — origin/destination inputs using geocoding autocomplete, swap button, add waypoint button, transport mode selector (car, bike, walk, truck icons), route summary (time, distance)
- Create src/components/routing/WaypointInput.tsx — input with geocoding autocomplete dropdown, clear button, drag handle for reorder
- Waypoint reorder via drag and drop
- Create src/components/routing/ModeSelector.tsx — transport mode buttons with icons

Phase 4 — Directions & Elevation:
- Create src/components/routing/DirectionsList.tsx — step-by-step maneuver list with icons (turn-left, turn-right, straight, etc.), distance per step, cumulative distance
- Create src/components/routing/ElevationProfile.tsx — SVG or canvas chart showing elevation along route (query terrain tiles for elevation data, or use Valhalla elevation if available)
- Create src/components/routing/RouteSummary.tsx — total time, distance, elevation gain/loss

Phase 5 — Isochrone Analysis:
- Create src/components/panels/IsochronePanel.tsx — origin input, time intervals (5, 10, 15, 30, 60 min), costing model selector
- Create src/components/map/layers/IsochroneLayer.tsx — renders isochrone polygons with graduated color bands (closer=darker emerald, farther=lighter)
- Support multiple origin points for comparison

Phase 6 — Advanced Features:
- Drag-to-reroute: make route line draggable, on drag create new waypoint and recalculate
- Alternative routes: request alternatives from Valhalla, show up to 3, click to select
- URL state: encode origin/destination/mode in URL search params for sharing

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Use `"use client"` only on components that need browser APIs.
- Dynamic import map layer components with `ssr: false`.
- Follow existing patterns (Zustand, tRPC routers, Zod).
```

### Prompt 3C — deck.gl Visualization (Track 03)

**Plugin:** `/ultrapilot`

> Can run in parallel with 3A and 3B.

```
Implement deck.gl Data Visualization (Track 03) — interleaved deck.gl v9 with MapLibre, 10+ layer types, interactivity.

Read conductor/tracks/03-deck-gl-visualization/spec.md and conductor/tracks/03-deck-gl-visualization/plan.md.

EXISTING CODE:
- package.json — has @deck.gl/core, @deck.gl/layers, @deck.gl/geo-layers, @deck.gl/mapbox, @deck.gl/react (all v9)
- src/components/map/MapView.tsx — MapLibre map with map context
- src/lib/map/map-context.ts — map instance context
- src/stores/map-store.ts — activeLayers array

IMPLEMENT ALL 6 PHASES:

Phase 1 — deck.gl Foundation:
- Create src/lib/map/deck-config.ts — deck.gl configuration: WebGL2 context sharing with MapLibre, default layer props, color scales
- Create src/components/map/DeckOverlay.tsx — uses MapboxOverlay from @deck.gl/mapbox in interleaved mode. Receives layers array prop. Registers as MapLibre custom control via map.addControl().
- Wire into MapView.tsx: after map loads, add DeckOverlay
- Create src/hooks/useDeckLayers.ts — hook that builds deck.gl layer array from active layers in store

Phase 2 — Core Visualization Layers:
- Create src/components/map/layers/HeatmapLayer.tsx — deck.gl HeatmapLayer config for fire risk data, configurable intensity/radius/colorRange
- Create src/components/map/layers/ScatterLayer.tsx — ScatterplotLayer for point data (sensors, POIs), size by property, color by category
- Create src/components/map/layers/GeoJsonLayer.tsx — generic GeoJsonLayer wrapper for arbitrary GeoJSON with auto-styling

Phase 3 — Advanced 3D Layers:
- Create src/components/map/layers/HexLayer.tsx — HexagonLayer for aggregation, 3D extruded hexagons, configurable radius/elevation scale
- Create src/components/map/layers/ColumnLayer.tsx — ColumnLayer for 3D bar charts at geo coordinates
- Create src/components/map/layers/ArcLayer.tsx — ArcLayer for flow visualization (origin→destination arcs), color gradient, animation
- Add TerrainExtension to all 3D layers for terrain draping

Phase 4 — Animation Layers:
- Create src/components/map/layers/TripsLayer.tsx — TripsLayer for animated vehicle/asset paths, time slider control
- Create src/components/map/layers/PathLayer.tsx — PathLayer for route/trail visualization with width by attribute
- Create src/components/ui/time-slider.tsx — time slider component for animation control (play, pause, speed, scrub)

Phase 5 — Interactivity:
- Create src/components/map/Tooltip.tsx — hover tooltip showing feature properties, positioned near cursor, styled with glassmorphic design
- Add onHover and onClick handlers to all deck.gl layers
- Click selection: highlight clicked feature, show detail panel
- Brush selection: rectangular selection to filter features within bounds

Phase 6 — Data Integration:
- Create src/lib/server/trpc/routers/visualization.ts — tRPC procedures: getHeatmapData, getPointData, getFlowData, getTimeSeriesData. Query PostGIS, return GeoJSON.
- Chunked loading for large datasets (pagination with bbox)
- Performance: use binary data format where possible, limit point count by zoom level

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Use `"use client"` on all deck.gl components (they need WebGL).
- Dynamic import DeckOverlay and all layer components with `ssr: false`.
- Follow existing patterns (Zustand, tRPC).
```

### Post-Sprint 3 — Build Fix

**Plugin:** `/build-fix`

```
Fix all TypeScript and build errors after implementing Tracks 07, 04, and 03. These tracks added tRPC infrastructure, routing, and deck.gl integration. Run `npx tsc --noEmit`, fix all errors with minimal changes. Pay special attention to:
- tRPC client/server type alignment
- deck.gl type imports (they can be tricky)
- MapLibre GL type compatibility
Do not alter business logic.
```

---

## SPRINT 4: Real-Time & 3D (Tracks 06 + 08 + 09)

### Prompt 4A — Real-Time Data Streaming (Track 06)

**Plugin:** `/ultrapilot`

```
Implement Real-Time Data Streaming (Track 06) — SSE, WebSocket, Redis pub/sub, live map updates.

Read conductor/tracks/06-realtime-data-streaming/spec.md and conductor/tracks/06-realtime-data-streaming/plan.md.

EXISTING CODE:
- src/lib/server/redis.ts — Redis client exists
- src/lib/server/db/ — Drizzle ORM with PostGIS
- src/lib/map/map-context.ts — map instance access
- src/lib/server/trpc/ — tRPC infrastructure
- docker-compose.yml — Redis service configured

IMPLEMENT ALL 6 PHASES:

Phase 1 — Redis Pub/Sub:
- Create src/lib/server/services/realtime.ts — Redis pub/sub manager: publish(channel, data), subscribe(channel, callback), unsubscribe(channel). Channels: layer:{layerId}, tracking:{assetId}, alerts:global. Geographic filtering: only publish to clients whose viewport intersects the data bbox.

Phase 2 — SSE Endpoints:
- Create src/app/api/stream/[layerId]/route.ts — SSE endpoint: sends GeoJSON feature updates for a specific layer. Uses ReadableStream with TextEncoder. Supports Last-Event-ID for reconnection. Heartbeat every 30s.
- Create src/hooks/useSSE.ts — generic SSE hook: connect to URL, parse events, auto-reconnect with exponential backoff (1s, 2s, 4s, 8s, max 30s), connection state tracking

Phase 3 — WebSocket Tracking:
- Create src/app/api/ws/route.ts — WebSocket endpoint for bidirectional tracking: receive position updates, broadcast to subscribers. Use Next.js API route with WebSocket upgrade.
- Create src/hooks/useWebSocket.ts — WebSocket hook: connect, send, receive, reconnect, heartbeat every 15s

Phase 4 — Live Map Layers:
- Create src/hooks/useLiveLayer.ts — combines useSSE with map layer updates: receives GeoJSON features via SSE, updates map source data incrementally (add/update/remove features without re-adding the entire source)
- Flash animation on new/updated features (brief highlight then fade)

Phase 5 — Data Ingest:
- Create src/app/api/ingest/sensors/route.ts — POST endpoint: validate sensor readings (Zod), store to PostGIS + TimescaleDB, publish to Redis
- Create src/app/api/ingest/fires/route.ts — POST endpoint: validate fire perimeter GeoJSON, store, publish
- Create src/lib/server/services/ingest.ts — shared ingest logic: validate, deduplicate (by ID + timestamp), store, publish

Phase 6 — Connection Management:
- Create src/components/map/LiveIndicator.tsx — small indicator showing connection state: green dot = live, yellow = reconnecting, red = offline. Uses the UI tooltip.
- Create src/stores/realtime-store.ts — Zustand: connections map, activeStreams, connectionState per stream
- Offline queue: buffer outgoing messages when disconnected, flush on reconnect

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT add comments, docstrings, or JSDoc unless the logic is genuinely non-obvious.
- Use `"use client"` only on hooks and components that run in browser.
- Follow existing patterns.
```

### Prompt 4B — Three.js 3D Objects (Track 08)

**Plugin:** `/ecomode`

> Smaller scope, self-contained. Ecomode saves tokens.

```
Implement Three.js Custom 3D Objects (Track 08) — GLTF models, animations, raycasting on MapLibre.

Read conductor/tracks/08-three-js-3d-objects/spec.md and conductor/tracks/08-three-js-3d-objects/plan.md.

EXISTING CODE:
- package.json — has three and @types/three
- src/lib/map/map-context.ts — map instance context
- src/components/map/MapView.tsx — MapLibre with WebGL2

IMPLEMENT ALL 6 PHASES:

Phase 1 — Three.js + MapLibre Integration:
- Create src/lib/map/three-utils.ts — utility functions: lngLatToMercator(lng, lat, altitude) → THREE.Vector3, createMercatorMatrix(map) for CustomLayerInterface, synchronize Three.js camera with MapLibre camera
- Create src/components/map/layers/ThreeLayer.tsx — implements MapLibre CustomLayerInterface: onAdd creates Three.js scene+camera+renderer (sharing WebGL2 context), render() syncs camera matrix and renders, onRemove cleans up

Phase 2 — GLTF Model Loading:
- Create src/components/map/layers/ModelLayer.tsx — loads GLTF models via GLTFLoader, places at geographic coordinates using three-utils transforms, supports scale/rotation/altitude props
- Create src/lib/map/model-library.ts — model catalog: name, url, default scale, default rotation for each model type (wind_turbine, fire_station, sensor_tower, vehicle, tree)
- Create public/models/ directory with placeholder .glb files (or document where to place them)

Phase 3 — Animation System:
- Add animation loop to ThreeLayer: requestAnimationFrame-based, only when animations are active
- Wind turbine: rotate blades continuously
- Pulsing beacon: Create src/components/map/layers/AnimatedBeacon.tsx — expanding translucent sphere with fade-out, for alert locations
- Route following: animate model along a path (array of coordinates) over time

Phase 4 — Procedural Geometry:
- 3D bar charts: create extruded cylinders/boxes at coordinates, height by data value
- Extruded polygons: take GeoJSON polygon, extrude to 3D with Three.js ExtrudeGeometry
- Volumetric effects: simple particle system for smoke/fire visualization

Phase 5 — Interaction:
- Raycasting: on mouse move, raycast into Three.js scene, detect hovered objects
- Hover: outline effect on hovered model (or emissive highlight)
- Click: show info popup (use map-popup UI component) with model properties

Phase 6 — Performance:
- LOD system: swap high-poly models for low-poly at distance
- Frustum culling: only render models in camera view
- Instanced rendering for repeated models (e.g., 100 trees = 1 instanced mesh)
- Dispose geometries/textures on cleanup

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Use `"use client"` on all Three.js components.
- Dynamic import ThreeLayer and ModelLayer with `ssr: false`.
```

### Prompt 4C — Drawing & Measurement (Track 09)

**Plugin:** `/ultrapilot`

```
Implement Drawing & Measurement Tools (Track 09) — 6 drawing modes, measurement, editing, annotations.

Read conductor/tracks/09-drawing-measurement/spec.md and conductor/tracks/09-drawing-measurement/plan.md.

EXISTING CODE:
- src/lib/map/map-context.ts — map instance context
- src/stores/map-store.ts — map state
- src/lib/server/trpc/ — tRPC infrastructure
- src/lib/server/trpc/routers/layers.ts — layer CRUD for saving drawings
- src/components/ui/ — full design system (floating-toolbar, button, slider, popover, dialog, toast)

IMPLEMENT ALL 6 PHASES:

Phase 1 — Drawing Foundation:
- Create src/stores/drawing-store.ts — Zustand: drawingMode (null|point|line|polygon|rectangle|circle|freehand), features[] (GeoJSON FeatureCollection), selectedFeatureIndex, undoStack[], redoStack[], isDrawing
- Create src/lib/map/drawing.ts — MapLibre event handlers: mousedown/mousemove/mouseup/click for each mode. Point: click to place. Line: click to add vertices, double-click to finish. Polygon: click vertices, click first point to close.
- Create src/hooks/useDrawing.ts — state machine hook managing drawing lifecycle, wires map events from drawing.ts to drawing-store

Phase 2 — Shape Drawing:
- Rectangle: click-drag to define bbox, render as polygon
- Circle: click center, drag for radius, render as 64-point polygon approximation
- Freehand: mousedown starts, mousemove adds points, mouseup finishes, simplify with Douglas-Peucker
- Create src/components/tools/DrawingToolbar.tsx — floating toolbar with mode buttons (point, line, polygon, rect, circle, freehand, select, delete). Uses floating-toolbar UI component.

Phase 3 — Measurement:
- Create src/lib/map/measurement.ts — geodesic calculations: distance (Haversine), area (spherical excess), bearing. Support unit conversion (m, km, mi, ft, acres, hectares).
- Create src/lib/geo/turf-helpers.ts — lightweight Turf.js-like helpers (or use @turf/helpers, @turf/area, @turf/length, @turf/distance if adding deps is ok — check package.json)
- Create src/hooks/useMeasurement.ts — hook that computes measurements for current drawing in real-time
- Create src/components/tools/MeasureTool.tsx — overlay showing distance/area as user draws, unit selector dropdown

Phase 4 — Editing:
- Create src/components/tools/VertexEditor.tsx — when a feature is selected: show vertex handles (small circles), drag to move vertices, click between vertices to add new one, click vertex + delete key to remove
- Feature translate: drag entire feature to move
- Feature rotate/scale: with modifier keys (shift+drag to rotate, alt+drag to scale)
- Undo/redo: push state to undoStack on every edit, Ctrl+Z/Ctrl+Y to navigate

Phase 5 — Annotations:
- Text labels: click to place, input text, font size, color picker
- Arrows: draw line with arrowhead at end
- Style editing: color, stroke width, fill opacity for any drawn feature
- Render annotations as MapLibre symbol/line layers

Phase 6 — Integration:
- Export: button to download current drawings as GeoJSON file
- Save to PostGIS: button to save drawings as a new layer via tRPC layers.create
- Import: accept pasted GeoJSON in a dialog, add to drawing canvas
- Elevation profile: for drawn lines, query terrain elevation at vertices and show profile chart

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Use `"use client"` on drawing components.
- Dynamic import drawing components with `ssr: false`.
- Consider installing @turf/helpers @turf/area @turf/length @turf/distance if not already present — check first.
```

### Post-Sprint 4 — Build Fix

**Plugin:** `/build-fix`

```
Fix all TypeScript and build errors after implementing Tracks 06, 08, and 09. Run `npx tsc --noEmit`, fix all errors. Pay attention to Three.js types, SSE/WebSocket types, and drawing state types. Minimal changes only.
```

---

## SPRINT 5: Domain Features (Tracks 10 + 14 + 13)

### Prompt 5A — Wildfire Prevention (Track 10)

**Plugin:** `/ultrapilot`

```
Implement the Wildfire Prevention Module (Track 10) — fire risk engine, strategy mapper, ecosystem tracker, weather.

Read conductor/tracks/10-wildfire-prevention/spec.md and conductor/tracks/10-wildfire-prevention/plan.md.

EXISTING CODE:
- src/components/map/layers/HeatmapLayer.tsx — deck.gl heatmap from Track 03
- src/hooks/useSSE.ts — SSE hook from Track 06
- src/hooks/useLiveLayer.ts — live layer updates from Track 06
- src/lib/server/trpc/ — tRPC infrastructure
- src/lib/server/db/schema.ts — Drizzle schema
- src/components/ui/ — full design system

IMPLEMENT ALL 6 PHASES:

Phase 1 — Fire Risk Engine:
- Create src/lib/server/services/fire-risk.ts — calculate fire risk score from: vegetation type, slope, aspect, historical fires, weather conditions. Return 0-100 risk score per grid cell.
- Create src/lib/server/services/nasa-firms.ts — fetch active fire data from NASA FIRMS API (https://firms.modaps.eosdis.nasa.gov/api/), parse CSV response, convert to GeoJSON points
- Create src/components/map/layers/FireRiskLayer.tsx — deck.gl HeatmapLayer showing fire risk, red-orange-yellow color ramp
- Store fire data in PostGIS: add fire_detections table to schema.ts

Phase 2 — Strategy Mapper:
- Create src/components/panels/StrategyPanel.tsx — panel showing 6 intervention strategies: firebreaks, controlled burns, vegetation management, water sources, evacuation routes, monitoring stations
- Create src/components/map/layers/InterventionLayer.tsx — renders strategy zones on map: polygons with pattern fills, icons for point strategies
- Each strategy type has: geometry (draw or select area), cost estimate, timeline, priority

Phase 3 — Weather Integration:
- Create src/lib/server/services/weather.ts — Open-Meteo API client: current conditions + 7-day forecast. Fields: temperature, humidity, wind speed/direction, precipitation, FWI (Fire Weather Index).
- Create src/components/map/layers/WeatherLayer.tsx — wind arrows overlay, temperature gradient, FWI zones
- Forecast slider: select day/hour to see predicted conditions

Phase 4 — Ecosystem Tracker:
- Create src/components/panels/EcosystemTracker.tsx — form to log ecosystem actions: reforestation, invasive species removal, soil restoration, water management, etc. Each action has: type, geometry (drawn on map), date, status, metrics (trees planted, area treated).
- Time-series charts (use Recharts or similar from existing deps, or simple SVG) showing cumulative impact
- Before/after comparison view

Phase 5 — Data Ingest:
- Create src/app/api/ingest/firms/route.ts — webhook endpoint for NASA FIRMS data push
- Periodic weather fetch (document cron setup for Open-Meteo polling every hour)
- NIFC fire perimeter import: fetch GeoJSON from NIFC API, store to PostGIS

Phase 6 — Dashboard:
- Create src/components/panels/FireDashboard.tsx — metrics overview: active fires count, highest risk areas, recent interventions, weather alerts
- Fire risk timeline chart: risk score over time for selected area
- Threshold alerts: when risk exceeds threshold, show toast notification

Add wildfire tRPC router: src/lib/server/trpc/routers/wildfire.ts with procedures for all CRUD operations.

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Use `"use client"` only where needed.
- Follow existing patterns.
```

### Prompt 5B — Fleet Tracking (Track 14)

**Plugin:** `/ultrapilot`

> Parallel with 5A.

```
Implement Fleet & Asset Tracking (Track 14) — real-time vehicle tracking, route history, geofencing, alerts.

Read conductor/tracks/14-fleet-tracking/spec.md and conductor/tracks/14-fleet-tracking/plan.md.

EXISTING CODE:
- src/lib/server/db/schema.ts — has tracking.positions table (TimescaleDB hypertable)
- src/hooks/useWebSocket.ts — WebSocket hook from Track 06
- src/hooks/useSSE.ts — SSE hook
- src/lib/server/services/realtime.ts — Redis pub/sub
- src/lib/server/trpc/ — tRPC infrastructure
- src/components/ui/ — full design system

IMPLEMENT ALL 6 PHASES:

Phase 1 — Position Ingestion:
- Enhance tracking schema in schema.ts: add assets table (id, name, type, status, metadata), geofences table (id, name, geometry as GeoJSON, alertOnEnter, alertOnExit)
- Create src/lib/server/services/tracking.ts — store positions to TimescaleDB, create continuous aggregates for last-known positions, query route history as PostGIS linestring
- WebSocket ingest: validate position payload (Zod), store, publish to Redis tracking channel

Phase 2 — Live Visualization:
- Create src/components/tracking/VehicleMarker.tsx — animated marker: vehicle icon rotated to heading, speed indicator, status color (green=moving, yellow=idle, red=offline)
- Smooth position interpolation between updates (lerp lat/lng over 1s)
- Create src/components/tracking/FleetPanel.tsx — sidebar list of vehicles: search, filter by status, click to center map on vehicle
- Create src/stores/tracking-store.ts — Zustand: vehicles map, selectedVehicleId, filters, geofences

Phase 3 — Route History:
- Query PostGIS for vehicle route as linestring within time range
- Create src/components/tracking/RouteHistory.tsx — time range selector, playback controls (play/pause/speed), time scrubber
- Render route on map: color-coded by speed (green=normal, yellow=slow, red=stopped)
- Stop detection: identify stops > 5 minutes, show as markers

Phase 4 — Geofencing:
- Create src/lib/server/services/geofence.ts — ST_Contains/ST_Intersects checks, entry/exit event detection comparing current vs previous position
- Create src/components/tracking/GeofenceEditor.tsx — draw geofence zones on map (reuse drawing tools from Track 09), name and configure alerts
- Trigger alerts on geofence entry/exit, publish via Redis

Phase 5 — Alert System:
- Create src/components/tracking/AlertManager.tsx — alert rules configuration: geofence breach, speed threshold, battery low, offline > N minutes
- Alert display: toast notifications + alert panel with history
- Acknowledgment flow: mark alerts as read/acknowledged

Phase 6 — Dashboard:
- Fleet stats in FleetPanel: total vehicles, active/idle/offline counts, average speed
- Zone reporting: vehicles per geofence zone
- Optimize for 500+ vehicles: use clustering at low zoom, individual markers at high zoom

Add tracking tRPC router: src/lib/server/trpc/routers/tracking.ts

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Use `"use client"` only where needed.
- Dynamic import map components with `ssr: false`.
```

### Prompt 5C — Analytics Dashboard (Track 13)

**Plugin:** `/ecomode`

> Parallel with 5A and 5B. Mostly UI charts, ecomode is fine.

```
Implement the Analytics Dashboard (Track 13) — configurable widgets, charts, spatial stats, export.

Read conductor/tracks/13-analytics-dashboard/spec.md and conductor/tracks/13-analytics-dashboard/plan.md.

EXISTING CODE:
- src/lib/server/trpc/ — tRPC infrastructure
- src/lib/server/db/ — Drizzle ORM + PostGIS
- src/hooks/useSSE.ts — for real-time metrics
- src/components/ui/ — full design system (tabs, dialog, sheet, button, skeleton)

Note: Do NOT add recharts or chart.js as a dependency. Use lightweight SVG-based charts built with React. If the complexity is too high, use a simple <canvas> approach.

IMPLEMENT ALL 6 PHASES:

Phase 1 — Dashboard Layout:
- Create src/app/dashboard/page.tsx — split layout: map on one side, widget grid on other
- Create src/components/dashboard/DashboardGrid.tsx — CSS grid of resizable, reorderable widget cards
- Dashboard presets: "Fire Monitoring", "Fleet Overview", "Environmental"
- Create src/components/dashboard/WidgetCard.tsx — card wrapper with title, resize handle, close button

Phase 2 — Chart Components:
- Create src/components/dashboard/TimeSeriesChart.tsx — SVG line/area chart with time axis, tooltip on hover
- Create src/components/dashboard/BarChart.tsx — horizontal/vertical bar chart
- Create src/components/dashboard/PieChart.tsx — donut/pie chart with labels
- Create src/components/dashboard/StatCard.tsx — single metric display: value, label, trend arrow, sparkline

Phase 3 — Spatial Analytics:
- Create src/lib/server/services/analytics.ts — PostGIS aggregate queries: point density (ST_ClusterKMeans), hotspot detection (kernel density), feature counts by polygon
- Create src/components/dashboard/SpatialStats.tsx — display spatial analysis results: cluster map, density overlay
- Create src/lib/server/trpc/routers/analytics.ts — tRPC procedures for all analytics queries

Phase 4 — Real-Time Metrics:
- Create src/components/dashboard/MetricsBar.tsx — horizontal bar of live counters: active fires, sensor alerts, fleet status, layer count
- Wire to SSE streams for real-time updates
- Status indicators (green/yellow/red dots)

Phase 5 — Export:
- CSV export: serialize widget data to CSV, trigger download
- Map screenshot: use map.getCanvas().toDataURL() for map image
- Simple PDF: generate HTML report layout, use window.print() with print styles (avoid heavy PDF libraries)

Phase 6 — Polish:
- Dashboard presets: save/load widget configurations to localStorage
- Mobile responsive: stack widgets vertically, collapse to single column
- Loading skeletons for each widget while data fetches

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Do NOT install new charting libraries — build SVG charts directly.
- Use `"use client"` on chart components.
```

### Post-Sprint 5 — Build Fix

**Plugin:** `/build-fix`

```
Fix all TypeScript and build errors after implementing Tracks 10, 14, and 13. Run `npx tsc --noEmit`, fix all errors. These tracks added wildfire, fleet tracking, and analytics. Minimal changes only.
```

---

## SPRINT 6: Enterprise (Tracks 12 + 18 + 19)

### Prompt 6A — Auth & Multi-Tenancy (Track 12)

**Plugin:** `/ultrapilot`

```
Implement Auth & Multi-Tenancy (Track 12) — NextAuth.js, RBAC, organizations, API keys.

Read conductor/tracks/12-auth-multitenancy/spec.md and conductor/tracks/12-auth-multitenancy/plan.md.

EXISTING CODE:
- src/lib/server/db/schema.ts — Drizzle schema (extend with auth tables)
- src/lib/server/trpc/ — tRPC infrastructure (add auth context)
- src/components/ui/ — design system
- package.json — check if next-auth is installed, if not note it needs adding

IMPLEMENT ALL 6 PHASES:

Phase 1 — NextAuth Setup:
- Install note: needs `next-auth@5` (NextAuth v5/Auth.js)
- Create src/app/api/auth/[...nextauth]/route.ts — NextAuth config with credentials provider (email/password) and placeholder OAuth (Google, GitHub — just config structure, actual keys from env)
- Add auth tables to schema.ts: users (id, name, email, password_hash, role, orgId), sessions, accounts, verification_tokens — following NextAuth Drizzle adapter pattern
- Create src/lib/server/auth.ts — getServerSession helper, hash/verify password utilities

Phase 2 — RBAC:
- Define roles: admin, editor, viewer, api_user
- Create permissions matrix: admin=all, editor=CRUD layers+features, viewer=read only, api_user=API access only
- Add role checks to tRPC context: create protectedProcedure and adminProcedure
- Create src/middleware.ts — Next.js middleware protecting /dashboard, /api/trpc (except public procedures)

Phase 3 — Multi-Tenancy:
- Add organizations table to schema.ts: id, name, slug, plan, settings
- Add orgId to layers, features tables (scope all queries by org)
- Create src/components/auth/OrgSwitcher.tsx — dropdown to switch between user's organizations
- Create src/lib/server/trpc/routers/org.ts — org CRUD, invite member, remove member

Phase 4 — API Security:
- Add api_keys table to schema.ts: id, key_hash, orgId, name, permissions, rate_limit, last_used
- Create API key auth middleware for /api/v1/* routes
- Rate limiting: track requests per key per minute in Redis, return 429 when exceeded

Phase 5 — User Management:
- Create src/components/auth/LoginForm.tsx — email/password login, OAuth buttons, register link
- Create src/components/auth/RegisterForm.tsx — registration form
- Create src/components/panels/UserPanel.tsx — user settings: profile, password change, org management
- Audit logging: add audit_logs table, log significant actions (layer create/delete, user invite, etc.)

Phase 6 — Integration:
- Update ALL existing tRPC routers to use protectedProcedure where appropriate
- Scope layer queries by orgId
- Add auth state to a Zustand store: src/stores/auth-store.ts

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Use `"use client"` only on form components.
- Follow existing patterns.
- Note any packages that need installing (next-auth, @auth/drizzle-adapter, bcryptjs) as comments at the top of the first file you create.
```

### Prompt 6B — Testing & Quality (Track 19)

**Plugin:** `/ultrapilot`

```
Implement Testing & Quality infrastructure (Track 19) — Vitest config, unit tests, integration tests, E2E setup.

Read conductor/tracks/19-testing-quality/spec.md and conductor/tracks/19-testing-quality/plan.md.

EXISTING CODE:
- package.json — has vitest and @testing-library/react in devDependencies
- tsconfig.json — strict mode enabled
- All application code from Sprints 1-6

IMPLEMENT PHASES 1-3 (setup and unit tests — E2E and performance are later):

Phase 1 — Setup:
- Create vitest.config.ts — configure Vitest with: jsdom environment, path aliases (@/), setup files, coverage thresholds
- Create src/test/setup.ts — test setup: mock maplibre-gl, mock ResizeObserver, mock IntersectionObserver, mock fetch
- Create src/test/utils.tsx — render helper wrapping components in providers (QueryClient, tRPC mock)
- Add test scripts to package.json: "test", "test:watch", "test:coverage"

Phase 2 — Unit Tests:
- Create src/__tests__/stores/map-store.test.ts — test all map store actions: setViewport, toggle3D, toggleGlobe, etc.
- Create src/__tests__/stores/drawing-store.test.ts — test undo/redo, feature add/remove
- Create src/__tests__/stores/routing-store.test.ts — test route state management
- Create src/__tests__/lib/map/measurement.test.ts — test distance, area, bearing calculations with known values
- Create src/__tests__/lib/server/services/geocoding.test.ts — test geocoding with mocked Photon responses
- Create src/__tests__/lib/server/services/routing.test.ts — test routing with mocked Valhalla responses

Phase 3 — Integration Tests:
- Create src/__tests__/trpc/layers.test.ts — test layer CRUD procedures with mocked DB
- Create src/__tests__/trpc/routing.test.ts — test routing procedures
- Create src/__tests__/api/geocode.test.ts — test geocode API route handler

Do NOT implement E2E tests (Phase 4), performance benchmarks (Phase 5), or CI pipeline (Phase 6) yet — those come with deployment.

RULES:
- Write code only. Do NOT run `npm run build`, `npm run dev`, `tsc`, or any test commands.
- Do NOT create git commits.
- Mock external services (Valhalla, Photon, Redis, PostGIS) — tests should not require running infrastructure.
- Use vi.mock() for module mocking.
```

### Prompt 6C — Railway Deployment Config (Track 18)

**Plugin:** `/ecomode`

> Config files only, ecomode is efficient.

```
Implement Railway Deployment Configuration (Track 18) — Dockerfiles, Railway config, CI/CD.

Read conductor/tracks/18-railway-deployment/spec.md and conductor/tracks/18-railway-deployment/plan.md.

EXISTING CODE:
- Dockerfile — exists (may need enhancement)
- docker-compose.yml — full local dev setup
- package.json — has build scripts

IMPLEMENT PHASES 1, 2, 4, 5 (skip Valhalla/Photon build and load testing):

Phase 1 — Dockerfile Enhancement:
- Enhance Dockerfile: multi-stage build (deps → build → runtime), use node:22-alpine, copy only necessary files, set NODE_ENV=production, expose port 3000, healthcheck endpoint
- Create .dockerignore — exclude node_modules, .git, .claude, conductor, data, etc.

Phase 2 — Railway Configuration:
- Create railway.json — service config for Railway Pro: build command, start command, health check path, environment variable references
- Create infra/railway/ directory with per-service notes (PostGIS, Martin, Valhalla, Photon, Redis, Next.js)
- Document required Railway environment variables in .env.example (enhance existing)

Phase 4 — Cloudflare R2:
- Enhance scripts/setup-pmtiles.sh — R2 bucket creation, PMTiles upload, CORS configuration example
- Create scripts/deploy-pmtiles.sh — upload script using rclone or aws cli

Phase 5 — CI/CD:
- Create .github/workflows/ci.yml — GitHub Actions: checkout, install deps, type-check (tsc --noEmit), lint (next lint), test (vitest run), build (next build). Run on push to main and PRs.
- Create .github/workflows/deploy.yml — on merge to main: build Docker image, deploy to Railway (using Railway GitHub integration trigger)

RULES:
- Write code only. Do NOT run any commands.
- Do NOT create git commits.
- Shell scripts: set -euo pipefail, proper quoting.
- CI workflows: use specific action versions (e.g., actions/checkout@v4).
```

### Post-Sprint 6 — Build Fix

**Plugin:** `/build-fix`

```
Fix all TypeScript and build errors after implementing Tracks 12, 19, and 18. Run `npx tsc --noEmit`, fix all errors. Auth integration may have broken existing tRPC routers. Ensure all test files compile. Minimal changes only.
```

---

## SPRINT 7: Platform (Tracks 16 + 17 + 20 + 11)

### Prompt 7A — Street-Level Imagery (Track 16)

**Plugin:** `/ecomode`

```
Implement Street-Level Imagery (Track 16) — Mapillary coverage layer, panorama viewer, sequence navigation.

Read conductor/tracks/16-street-view-imagery/spec.md and conductor/tracks/16-street-view-imagery/plan.md.

EXISTING CODE:
- src/lib/map/map-context.ts — map instance
- src/components/ui/ — design system (dialog, sheet)
- src/components/layout/ — MapLayout with SidePanel

IMPLEMENT PHASES 1-4 (core functionality):

Phase 1 — Mapillary API:
- Create src/lib/server/services/mapillary.ts — Mapillary Graph API v4 client: getImages(bbox, limit), getImageById(id), getSequence(sequenceId). API key from env MAPILLARY_ACCESS_TOKEN.

Phase 2 — Coverage Layer:
- Create src/components/imagery/StreetCoverage.tsx — add Mapillary vector tile layer to map showing coverage as green lines/dots. Filter by date range.

Phase 3 — Panorama Viewer:
- Create src/components/imagery/PanoViewer.tsx — 360 panoramic image viewer using a lightweight equirectangular renderer (canvas-based). Navigation arrows to step through sequence. Compass showing current viewing direction.
- Note: If Photo Sphere Viewer is too heavy, use a simple Three.js sphere with equirectangular texture.

Phase 4 — Map Integration:
- Click on coverage layer → open PanoViewer in split view (map left, panorama right)
- Mini-map in panorama view showing current position
- Bidirectional sync: rotate panorama → update map bearing, move on map → update panorama

RULES:
- Write code only. Do NOT run any commands.
- Do NOT create git commits.
- Use `"use client"` on viewer components.
- Dynamic import PanoViewer with `ssr: false`.
```

### Prompt 7B — Places & POI (Track 17)

**Plugin:** `/ecomode`

```
Implement Places & Points of Interest (Track 17) — POI search, categories, nearby places.

Read conductor/tracks/17-places-poi/spec.md and conductor/tracks/17-places-poi/plan.md.

EXISTING CODE:
- src/lib/server/services/geocoding.ts — forward/reverse geocoding
- src/lib/server/db/schema.ts — Drizzle schema
- src/lib/server/trpc/ — tRPC infrastructure
- src/components/ui/ — design system

IMPLEMENT PHASES 1-4:

Phase 1 — POI Data:
- Add poi table to schema.ts: id, name, category, subcategory, geometry (point), address, phone, website, hours, tags (jsonb), osm_id
- Create src/lib/server/services/places.ts — PostGIS queries: searchByCategory(category, bbox), searchNearby(lat, lon, radius, limit), searchByText(query, bbox), with pg_trgm fuzzy matching

Phase 2 — Search:
- Create src/lib/server/trpc/routers/places.ts — tRPC: search, getById, nearby, categories procedures

Phase 3 — UI:
- Create src/components/places/PlacesPanel.tsx — search input + category chips + results list
- Create src/components/places/PlaceCard.tsx — POI detail: name, category icon, address, distance, phone, website, hours
- Create src/components/places/CategoryFilter.tsx — horizontal scrollable chip bar: restaurants, shops, parks, transit, hospitals, schools, fire stations, water sources
- Create src/components/places/NearbyPlaces.tsx — "What's nearby" panel showing POIs grouped by category

Phase 4 — Map Integration:
- POI markers on map with category icons (lucide-react)
- Clustering at low zoom (MapLibre native clustering)
- Click marker → show PlaceCard popup

RULES:
- Write code only. Do NOT run any commands.
- Do NOT create git commits.
```

### Prompt 7C — Embed & API (Track 20)

**Plugin:** `/ecomode`

```
Implement Embeddable Map & Public API (Track 20) — embed widget, REST API, documentation.

Read conductor/tracks/20-embed-api/spec.md and conductor/tracks/20-embed-api/plan.md.

EXISTING CODE:
- All application code from previous sprints
- src/lib/server/trpc/ — tRPC infrastructure
- src/lib/server/services/ — all services

IMPLEMENT PHASES 1-4:

Phase 1 — Embed Widget:
- Create src/app/embed/page.tsx — minimal map view with URL params: center (lat,lng), zoom, layers, style, markers. No sidebar, no panels. Responds to parent window postMessage for control.
- Create src/app/embed/layout.tsx — minimal layout without navigation
- Embed code generator: snippet that produces <iframe> HTML

Phase 2 — Public API:
- Create src/app/api/v1/features/route.ts — OGC Features-like API: GET with bbox, limit, offset, properties filter. Returns GeoJSON FeatureCollection. Requires API key header.
- Create src/app/api/v1/layers/route.ts — list available layers

Phase 3 — Proxy APIs:
- Create src/app/api/v1/route/route.ts — public routing API proxying Valhalla, API key auth, rate limited
- Create src/app/api/v1/geocode/route.ts — public geocoding API proxying Photon, API key auth

Phase 4 — Documentation:
- Create src/app/docs/page.tsx — API documentation page with endpoint descriptions, request/response examples, authentication instructions
- Interactive "try it" forms for each endpoint

RULES:
- Write code only. Do NOT run any commands.
- Do NOT create git commits.
- API routes should validate API key from x-api-key header.
- Return proper HTTP status codes and error JSON.
```

### Prompt 7D — Offline & PWA (Track 11)

**Plugin:** `/ecomode`

```
Implement Offline-First & PWA (Track 11) — service worker, tile caching, offline data, background sync.

Read conductor/tracks/11-offline-pwa/spec.md and conductor/tracks/11-offline-pwa/plan.md.

EXISTING CODE:
- src/app/layout.tsx — root layout
- All map and data components

IMPLEMENT PHASES 1-4:

Phase 1 — PWA Foundation:
- Create src/app/manifest.ts — Next.js metadata manifest: name, short_name, icons, theme_color (slate-900), background_color, display: standalone
- Create public/sw.js — service worker: install (precache app shell), activate (clean old caches), fetch (routing strategy)
- Register service worker in layout.tsx
- App icons: document required icon sizes (192x192, 512x512)

Phase 2 — Tile Caching:
- In sw.js: cache-first strategy for basemap tiles (PMTiles, OSM raster), network-first for dynamic Martin tiles
- LRU eviction when cache exceeds 500MB
- Cache versioning for updates

Phase 3 — Offline Data:
- Create src/lib/offline/indexed-db.ts — IndexedDB helpers: openDB, getStore, put, get, getAll, delete
- Create src/lib/offline/sync-queue.ts — queue offline operations (feature creates/edits) for later sync
- Store drawn features and edits in IndexedDB when offline

Phase 4 — Background Sync:
- Create src/hooks/useOfflineSync.ts — detect online/offline, flush sync queue when back online
- Create src/components/ui/SyncIndicator.tsx — small badge showing sync status: synced, pending (count), syncing
- Conflict resolution: last-write-wins for simple cases

RULES:
- Write code only. Do NOT run any commands.
- Do NOT create git commits.
- Service worker must be vanilla JS (not TypeScript) in public/.
```

### Post-Sprint 7 — Final Build Fix

**Plugin:** `/build-fix`

```
Final pass: fix ALL TypeScript and build errors across the entire codebase. Run `npx tsc --noEmit` and fix everything. Then run `npx next lint` and fix lint errors. This is the final cleanup before developer review.
```

---

## FINAL VALIDATION

### Final Type Check + Test Run

**Run manually (not a plugin prompt):**

```bash
npm run type-check
npm run lint
npm test
```

### Optional: Code Review

**Plugin:** `/code-review`

```
Review the entire PlantGeo codebase for: security vulnerabilities (especially in API routes, auth, data ingest endpoints), performance issues (unnecessary re-renders, missing memoization on map components, large bundle imports), and architectural consistency (all tRPC routers follow same patterns, all stores follow same patterns, all map layers follow same patterns). Focus on high-severity issues only.
```

---

## Quick Reference: Plugin Cheat Sheet

| Sprint | Tracks | Primary Plugin | Parallel? |
|--------|--------|---------------|-----------|
| 1 | 15 → 01 | `/ultrapilot` | Sequential (01 needs 15) |
| 2 | 05 + 02 | `/ultrapilot` + `/ecomode` | Parallel |
| 3 | 07 + 04 + 03 | `/ultrapilot` x3 | Parallel |
| 4 | 06 + 08 + 09 | `/ultrapilot` + `/ecomode` + `/ultrapilot` | Parallel |
| 5 | 10 + 14 + 13 | `/ultrapilot` x2 + `/ecomode` | Parallel |
| 6 | 12 + 19 + 18 | `/ultrapilot` x2 + `/ecomode` | Parallel |
| 7 | 16 + 17 + 20 + 11 | `/ecomode` x4 | Parallel |
| End | — | `/build-fix` then `/code-review` | Sequential |

### Between-Sprint Workflow
1. Run the sprint prompts (parallel where noted)
2. Run `/build-fix` to clean up type errors
3. Manually verify: `npm run type-check && npm run lint`
4. Review changes, stage, and commit at your discretion
5. Proceed to next sprint
