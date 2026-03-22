# PlantGeo: Environmental Intelligence Platform — Product Spec

**Source:** deep-dive interview (trace-injected)
**Ambiguity at crystallization:** ~13%
**Date:** 2026-03-21

---

## Goal

Build PlantGeo into a **comprehensive, competitive environmental intelligence platform** that aggregates global open-source geospatial datasets (wildfire, water scarcity, soil health, vegetation/land cover) and makes them actionable — showing communities, landowners, NGOs, government agencies, and affected industries **exactly where to implement restoration and risk-mitigation strategies**, and **who can help them do it** (PlantCommerce/Aevani and other teams on the platform).

PlantGeo is the **geospatial intelligence layer**. PlantCommerce/Aevani is the **economic engine** — a team on PlantGeo whose products and services are surfaced within PlantGeo's strategy cards via embedded supplier links. The platforms remain architecturally separate but are functionally integrated through the strategy→supplier handoff.

---

## Non-Goals

- **No carbon credit issuance, MRV, or DAO governance in PlantGeo** — this lives in PlantCommerce/Aevani
- **No raw satellite imagery ingestion** — use pre-computed indices (NDVI, NBR, NDWI) as tile overlays from Copernicus/GEE/USGS
- **No proprietary data collection** — all data from publicly available open sources
- **No marketplace transactions in PlantGeo** — links hand off to PlantCommerce/Aevani for purchasing

---

## Target Users

All of the following, with no barriers to entry:
1. **General public & community activists** — submit strategy request pins, vote on priority areas, follow local environmental conditions
2. **Landowners & ranchers** — understand what interventions their land is suitable for, what risks exist, connect to suppliers
3. **Conservation orgs & NGOs** — regional data for funding prioritization, intervention opportunity mapping, progress tracking
4. **Government agencies & fire districts** — wildfire risk mapping, resource pre-positioning, community alerts, post-fire recovery monitoring
5. **Utility companies** — infrastructure risk from wildfire and water scarcity
6. **Affected industries** — loggers, builders, and others who need to know where risk is highest and what mitigation options exist

---

## Geographic Scope

**Phase 1:** Western USA — California, Oregon, Washington, Arizona (richest LANDFIRE, PRISM, USGS NWIS, NASA FIRMS data density)
**Phase 2:** Global expansion using globally available datasets (SoilGrids 250m, WorldClim, HydroSHEDS, NASA FIRMS) at coarser resolution
**Expansion trigger:** When community requests cluster in a new region, prioritize adding higher-resolution local data for that area

---

## Core Features (v1 — Comprehensive from Day 1)

### 1. Environmental Risk Data Layers

All four domains loaded simultaneously as toggleable map layers, derived from open data:

**A. Wildfire Risk Layer**
- Replace hardcoded vegetation weights in `calculateFireRisk()` with real LANDFIRE fuel model data (LF 2020, EVT codes)
- Live fire detection: NASA FIRMS VIIRS NRT (60-second refresh) — already partially implemented
- Fire behavior risk scoring: fuel type × slope × aspect × wind × humidity × drought index
- Burn severity history: MTBS (Monitoring Trends in Burn Severity) — polygon overlays of historical burn perimeters
- Post-fire recovery: pre-computed NBR (Normalized Burn Ratio) tiles from USGS/Copernicus

**B. Water Scarcity Layer**
- USGS NWIS streamflow gauges as live markers (flow rate, trend: rising/falling/critical)
- US Drought Monitor weekly drought classification (D0-D4) as choropleth overlay
- HydroSHEDS watershed catchment boundaries as vector layer
- Groundwater level trend indicators from USGS groundwater monitoring network

**C. Vegetation & Land Cover Layer**
- NDVI (Normalized Difference Vegetation Index) monthly composite as raster tile overlay — from Copernicus Land Service (pre-computed, free)
- NLCD (National Land Cover Database) classification as base reference layer
- Land use change detection: annual NLCD change comparison (deforestation, urbanization, recovery)
- Reforestation opportunity zones: derived from soil suitability + land cover + fire history

**D. Soil Health & Carbon Potential Layer**
- SoilGrids 250m: organic carbon, clay content, pH, bulk density as choropleth layers
- USDA Web Soil Survey: dominant soil series, drainage class, hydric soil designation as vector tiles
- Soil erosion risk: USLE/RUSLE-derived erosion hazard rating
- Carbon sequestration potential: derived composite (organic carbon + land cover + precipitation)

### 2. Community Strategy Request System

**Pin submission**: Any user (logged in or anonymous with rate limiting) can:
- Drop a pin anywhere on the map
- Tag a strategy type: `reforestation | water_harvesting | firebreak | soil_restoration | wetland_restoration | monitoring_station | other`
- Add title, description (max 500 chars), optional photos (uploaded to R2)
- Submit for immediate public visibility

**Voting**: Any registered user can upvote/downvote a pin once. Pins display vote count.

**Priority surfacing**: High-vote density areas (>5 pins within 10km radius, avg votes >10) automatically generate a "Community Priority Zone" polygon that appears as a dedicated map layer — showing decision-makers where communities are asking for help.

**Schema additions needed:**
```
strategyRequests: id, lat, lon, strategyType, title, description, imageUrls[], userId (nullable), votes, createdAt
strategyVotes: requestId, userId, voteType (up/down), createdAt
```

### 3. Team & Organization Pages

**Team profiles on the map**: Teams registered on PlantGeo can:
- Set a service area (polygon drawn on map, or select counties/states)
- Tag their specialties: `supplier | ngo | government | research | landowner | contractor`
- List contact info, website, brief description
- Mark themselves as "active in this region"

**PlantCommerce/Aevani as the flagship team**: The primary example — their service area covers Western USA, specialty = `supplier`, their strategy cards appear in the supplier links panel.

**Discovery**: Users can filter the map by team type and specialty to find who's operating in their area.

### 4. Strategy Cards with Embedded Supplier Links

When a user clicks any area of the map (or a community request pin), a **Strategy Card** appears with:
- Environmental conditions for that location (fire risk score, drought index, soil type, vegetation health)
- Recommended strategies for those conditions (ranked by suitability)
- **"Get supplies & services for this strategy"** section — embedded links to PlantCommerce/Aevani products tagged to that strategy type
- Community requests in the vicinity (vote count, proximity)
- Nearby teams offering services for this strategy type

**PlantCommerce integration**: PlantGeo exposes a public API endpoint:
```
GET /api/v1/strategy-suppliers?strategy={type}&lat={}&lon={}
```
PlantCommerce/Aevani registers their products against strategy types. This endpoint returns matched suppliers. PlantGeo calls it when rendering strategy cards.

### 5. Real-Time Alert System

**Wildfire alerts**: When NASA FIRMS detects active fire within user-configured radius of a pinned location → SSE push notification (existing `useSSE` hook) + optional email
**Water emergency alerts**: When USGS streamflow drops below critical threshold at a monitored gauge → alert for affected watershed
**Community alerts**: When a high-vote community priority zone forms near user's area → notification

---

## Data Sources & Integration

| Domain | Primary Source | Format | Update Cadence | Cost |
|--------|---------------|--------|---------------|------|
| Fire detection | NASA FIRMS API | GeoJSON/CSV | 60-second NRT | Free |
| Fire risk fuels | LANDFIRE LF2020 | WMS tiles / GeoTIFF | Annual release | Free |
| Burn history | MTBS | Shapefile/GeoJSON | Annual | Free |
| Drought | US Drought Monitor | GeoJSON/Shapefile | Weekly | Free |
| Streamflow | USGS NWIS API | JSON timeseries | 15-minute intervals | Free |
| Watersheds | HydroSHEDS | GeoJSON vector | Static | Free |
| NDVI tiles | Copernicus Land Service | COG tile overlay | Monthly composite | Free |
| Land cover | NLCD | WMS/GeoTIFF | Biennial | Free |
| Soil properties | SoilGrids API | REST + GeoTIFF | Annual | Free |
| Soil survey | USDA WSS SOAP/REST | GeoJSON | Annual | Free |
| Climate baseline | WorldClim v2.1 | GeoTIFF | Static baseline | Free |
| Satellite indices | USGS EarthExplorer / Copernicus | Pre-computed GeoTIFF/XYZ tiles | Monthly | Free |

---

## Acceptance Criteria

1. **All 4 environmental data domains** visible as toggleable map layers on first load for Western USA region
2. **Fire risk score** uses real LANDFIRE EVT fuel model codes (not hardcoded weights) — fire risk recalculates correctly for any point in CONUS
3. **Community strategy request pins** can be submitted by any visitor, display vote counts, and high-density clusters generate Priority Zone polygons automatically
4. **Team pages** are visible on the map — PlantCommerce/Aevani appears as a team with Western USA service area and strategy-tagged products
5. **Strategy cards** appear on map click with location conditions + ranked strategies + supplier links calling `/api/v1/strategy-suppliers`
6. **USGS streamflow gauges** appear as live markers updating at 15-minute intervals with trend indicators
7. **NDVI monthly composite** tiles load as a raster overlay in MapLibre without infrastructure changes (XYZ tile URL from Copernicus)
8. **Real-time wildfire alerts** fire via SSE when NASA FIRMS detects fire within user-configured radius
9. **All features accessible without login** (read-only) — login required only for voting, submitting pins, and team management

---

## Assumptions Exposed

- LANDFIRE WMS tiles can be loaded directly into MapLibre as an XYZ tile source without proxy
- Copernicus Land Service NDVI monthly composites are available as XYZ tile endpoints (they are: `https://s2cloudless.sentinel-hub.com/...` or similar)
- SoilGrids REST API has sufficient rate limits for map-interactive queries (they cap at ~2 req/sec — will need tile caching in Redis)
- USGS NWIS API sustains 15-minute polling for ~500 gauges in Western USA without rate limiting
- The existing PostGIS + Martin + Redis + Next.js stack handles these additions without re-architecture (confirmed by trace: vector-first strategy with pre-computed raster overlays avoids COG/STAC infrastructure)

---

## Technical Context

**What exists and can be leveraged:**
- `nasa-firms.ts` service — extend with LANDFIRE integration
- `weather.ts` service — add drought monitor polling
- `wildfire.ts` tRPC router — add `getLandFireRisk`, `getDroughtIndex`, `getStreamflow`
- `layers` + `features` schema — JSONB supports any new data type without migration
- Redis pub/sub — extend for watershed alerts
- Existing `getSources()` in `sources.ts` — add NDVI tile overlay source, NLCD WMS source
- `getLayers()` in `layers.ts` — add soil, water, vegetation layer specifications
- `contributions.ts` router — refactor to power strategy request pins + voting
- `teams.ts` router — extend with service area polygon + specialty tags

**What needs to be added:**
- `strategyRequests` and `strategyVotes` tables in schema
- `src/lib/server/services/landfire.ts` — LANDFIRE WMS/REST integration
- `src/lib/server/services/usgs-water.ts` — NWIS streamflow polling
- `src/lib/server/services/soilgrids.ts` — SoilGrids REST API client
- `src/lib/server/services/drought.ts` — US Drought Monitor GeoJSON poller
- `src/lib/server/trpc/routers/environmental.ts` — unified router for all new data domains
- `src/lib/server/trpc/routers/strategy-requests.ts` — CRUD + voting for community pins
- `src/components/map/layers/WaterLayer.tsx` — streamflow markers + watershed polygons
- `src/components/map/layers/SoilLayer.tsx` — soil choropleth
- `src/components/map/layers/VegetationLayer.tsx` — NDVI tile overlay + NLCD
- `src/components/panels/StrategyCard.tsx` — location conditions + recommendations + supplier links
- `src/components/panels/CommunityPanel.tsx` — pin submission form + nearby requests
- `src/app/api/v1/strategy-suppliers/route.ts` — supplier link API for PlantCommerce

---

## Ontology

| Term | Definition |
|------|-----------|
| **Strategy Request** | Community-submitted map pin tagging a location as needing a specific restoration/mitigation strategy |
| **Priority Zone** | Auto-generated polygon from high-density, high-vote strategy request clusters |
| **Strategy Card** | UI panel showing environmental conditions + recommendations + supplier links for a clicked map location |
| **Team** | Organization registered on PlantGeo with a defined service area and specialty tags |
| **Environmental domain** | One of four risk categories: wildfire, water scarcity, soil health, vegetation/land cover |
| **Supplier link** | Embedded reference to PlantCommerce/Aevani products tagged to a strategy type |
| **Fuel model** | LANDFIRE EVT (Existing Vegetation Type) classification used for fire behavior modeling |
| **Pre-computed index** | Satellite-derived metric (NDVI, NBR, NDWI) generated externally, served as tile overlay |

---

## Trace Findings

**Most likely explanation (from trace):** PlantGeo covers ~30% of the knowledge base vision — strong geospatial MVP (wildfire detection, 3D visualization, real-time tracking) but missing environmental data layers (soil, water, vegetation), community input mechanisms, and the PlantCommerce integration touchpoint. Three gaps compound sequentially: data foundation → strategy recommendations → supplier connections.

**Per-lane critical unknowns resolved:**
- Lane 1 (Data coverage): Resolved — all 4 domains from open data, pre-computed indices avoid satellite ingestion infrastructure
- Lane 2 (Market mechanics): Resolved — economics stay in PlantCommerce; PlantGeo integrates via `/api/v1/strategy-suppliers` endpoint
- Lane 3 (Scale/architecture): Resolved — pre-computed raster overlays (XYZ tiles) avoid COG/STAC infrastructure; current stack sufficient

**Evidence that shaped this spec:**
- Zero economic tables in PlantGeo schema → confirmed non-goal
- `calculateFireRisk()` uses hardcoded vegetation weights → confirmed upgrade to LANDFIRE
- Generic JSONB layers/features schema → confirmed extensible without migrations
- Martin v1.4 vector-only config + pre-computed index decision → confirmed current architecture is sufficient for v1

---

## Interview Transcript Summary

1. **Platform architecture**: PlantGeo = geospatial intelligence; PlantCommerce/Aevani = economic engine as a team ON PlantGeo, connected via embedded supplier links
2. **Satellite data**: Pre-computed indices (NDVI, NBR, NDWI) as XYZ tile overlays — no raw ingestion
3. **Data priorities**: All four domains equally — wildfire, water, vegetation, soil — all from open data
4. **Community requests**: Map-pinned with voting + auto-generated Priority Zone polygons from clusters
5. **Geographic scope**: Western USA first, globally expandable
6. **Target users**: All stakeholders — public, landowners, NGOs, government, utility companies, affected industries
7. **PlantCommerce link**: Embedded supplier links in strategy cards via `/api/v1/strategy-suppliers`
8. **v1 scope**: Comprehensive from day 1 — all four data domains + community pins + team pages + supplier links
