# Backend Services Reference

PlantGeo includes 30+ backend services that integrate with external APIs, perform geospatial computations, and manage system state. All services are located in `/src/lib/server/services/`.

## Fire Detection & Analysis Services

### nasa-firms.ts

Fetch active fire detections from NASA FIRMS API.

**Key Functions:**
- `fetchActiveFiresNASA(bbox?: string, dayRange?: number): Promise<GeoJSON.FeatureCollection>`

**Purpose:** Ingest real-time fire detections from MODIS and VIIRS satellites.

**External API:** NASA FIRMS EOSDIS API
- Endpoint: `https://firms.modaps.eosdis.nasa.gov/api/country/csv`
- Auth: API key in `NASA_FIRMS_KEY`

**Data returned:**
- Latitude, longitude
- Detection confidence
- Brightness (radiative temperature)
- Fire Radiative Power (FRP)

**Caching:** Redis key `fire:detections:{bbox}`, TTL 30 minutes

**Rate limiting:** 1 request per minute to NASA API

---

### fire-risk.ts

Calculate fire risk score based on terrain and weather conditions.

**Key Functions:**
- `calculateFireRisk(params: FireRiskParams): number`

**Purpose:** Compute 0-100 fire risk score using NFDRS (National Fire Danger Rating System) methodology.

**Inputs:**
- Vegetation type (mixed_forest, grassland, shrubland, etc)
- Slope (0-90 degrees)
- Aspect (0-360 degrees)
- Relative humidity (0-100%)
- Wind speed (km/h)
- Fuel load factor (optional)

**Algorithm:** Weighted combination of:
- Vegetation flammability
- Topographic factors (slope, aspect)
- Weather conditions (humidity, wind)

**Output:** 0-100 risk score (0=safe, 100=extreme danger)

**Caching:** No caching (lightweight computation)

---

### fire-weather-index.ts

Calculate Canadian Fire Weather Index (FWI) system components.

**Key Functions:**
- `calculateFullFWI(weather: WeatherInputs, previousFWI?: FWIState): FWIComponents`

**Purpose:** Compute FWI indices used in fire prediction models.

**Inputs:**
- Temperature (°C)
- Relative humidity (0-100%)
- Wind speed (km/h)
- Precipitation (mm)
- Month (1-12)
- Previous FWI state (optional)

**Components calculated:**
- FFMC: Fine Fuel Moisture Code (0-101)
- DMC: Duff Moisture Code (0-650)
- DC: Drought Code (0-1200)
- ISI: Initial Spread Index (0-50)
- BUI: Buildup Index (0-400)
- FWI: Fire Weather Index (0-100+)

**External Reference:** Canadian Forest Fire Weather Index (FWI) System

**Caching:** Per-location weather cache

---

### landfire.ts

Get vegetation and fuel data from LANDFIRE dataset.

**Key Functions:**
- `getLandFireEVT(lat: number, lon: number): Promise<EVTData>`

**Purpose:** Retrieve vegetation and fuel type information for fire modeling.

**External API:** LANDFIRE WMS/API
- Endpoint: `https://lfps.usgs.gov/`
- Data: Existing Vegetation Type (EVT)

**Data returned:**
- Vegetation type classification
- Fuel load parameters
- Canopy height, density

**Caching:** Redis key `landfire:evt:{lat},{lon}`, TTL 24 hours

---

### mtbs.ts

Get Monitoring Trends in Burn Severity (MTBS) data.

**Key Functions:**
- `getMTBSPerimeters(bbox?: string): Promise<GeoJSON.FeatureCollection>`

**Purpose:** Access historical burn perimeters and severity data.

**External API:** USGS MTBS
- Endpoint: `https://www.mtbs.gov/api`

**Data returned:**
- Burn perimeters (polygons)
- Severity classification (low, medium, high, increased greenness)
- Burn year and date

**Caching:** Redis key `mtbs:perimeters:{bbox}`, TTL 24 hours

---

## Water & Hydrology Services

### usgs-water.ts

Get water gauge data and streamflow measurements.

**Key Functions:**
- `getWaterGaugeData(siteCode: string): Promise<WaterGaugeReading>`
- `getStreamflowAlerts(bbox: string): Promise<AlertList>`

**Purpose:** Monitor water levels, streamflow, and drought conditions.

**External API:** USGS Water Services
- Endpoint: `https://waterservices.usgs.gov/nwis/iv`
- Parameters: Real-time instantaneous values

**Data returned:**
- Station ID, name, location
- Current stage (water level)
- Discharge (streamflow)
- Temperature

**Alert Logic:** Compare to historical thresholds (percentiles)
- Below 10th percentile: Drought alert
- Above 90th percentile: Flood alert

**Caching:** Redis key `water:gauge:{siteCode}`, TTL 1 hour

**Update frequency:** Hourly background job (water-refresh.ts)

---

### hydrosheds.ts

Get watershed boundaries and hydrological data.

**Key Functions:**
- `getWatershedForPoint(lat: number, lon: number): Promise<Watershed>`

**Purpose:** Identify watershed and upstream/downstream catchments.

**External API:** HydroSHEDS dataset (PostGIS local copy)
- Data: Vector watershed boundaries
- Aggregated locally (no external API calls)

**Data returned:**
- Watershed polygon
- Upstream catchments
- Stream order
- Flow accumulation

**Caching:** Per-query result cached locally

---

### drought.ts

Monitor drought conditions and indices.

**Key Functions:**
- `getDroughtStatus(lat: number, lon: number): Promise<DroughtIndex>`

**Purpose:** Track drought severity via multiple indices.

**Data Sources:**
- NOAA Drought Monitor (weekly polygons)
- USGS water data (streamflow percentiles)
- NDVI vegetation stress (via vegetation.ts)

**Output:** Composite drought severity (0-100)

**Caching:** Redis key `drought:status:{lat},{lon}`, TTL 24 hours

---

## Vegetation & Land Cover Services

### vegetation.ts

Get vegetation health and biomass data.

**Key Functions:**
- `getVegetationIndex(lat: number, lon: number): Promise<VegetationMetrics>`
- `getNDVITimeSeries(bbox: string, startDate: Date, endDate: Date): Promise<NDVIData[]>`

**Purpose:** Monitor vegetation health via satellite vegetation indices.

**Indices calculated:**
- NDVI: Normalized Difference Vegetation Index (-1 to +1)
- EVI: Enhanced Vegetation Index (improved over NDVI)
- LAI: Leaf Area Index

**Data Sources:**
- Sentinel-2 (10m resolution, 5-day revisit)
- MODIS (250m-1km, daily)
- Landsat (30m, 16-day)

**Output:**
- Current NDVI/EVI values
- 30-day trend
- Anomaly from historical mean

**Caching:** Redis key `vegetation:ndvi:{lat},{lon}`, TTL 24 hours

---

### nlcd.ts

Get National Land Cover Database (NLCD) classification.

**Key Functions:**
- `getNLCDLandCover(lat: number, lon: number): Promise<LandCoverClass>`

**Purpose:** Retrieve land cover type at a location.

**Classification:**
- Water
- Developed (impervious surface %)
- Forest (type: deciduous, coniferous, mixed)
- Grassland/herbaceous
- Cultivated
- Wetland

**Data Source:** USGS NLCD (30m resolution, updated ~2-3 years)

**External API:** Not directly; data served via WMS
- Endpoint: `https://gisdata.usgs.gov/`

**Caching:** Redis key `nlcd:lc:{lat},{lon}`, TTL 30 days (static)

---

## Soil & Terrain Services

### soilgrids.ts

Get soil properties from ISRIC SoilGrids.

**Key Functions:**
- `getSoilProperties(lat: number, lon: number, depth?: number): Promise<SoilData>`

**Purpose:** Retrieve soil characteristics for environmental analysis.

**Properties returned:**
- Soil texture (sand, silt, clay %)
- pH
- Organic carbon content
- Bulk density
- Available water capacity

**Depths:** 0-5cm, 5-15cm, 15-30cm, 30-60cm, 60-100cm

**External API:** ISRIC SoilGrids REST API
- Endpoint: `https://rest.isric.org/soilgrids/v2.0/properties/query`

**Caching:** Redis key `soil:properties:{lat},{lon}`, TTL 30 days

---

### usda-soil.ts

Get USDA soil survey data and classifications.

**Key Functions:**
- `getUSDAMapunit(lat: number, lon: number): Promise<MapunitData>`

**Purpose:** Detailed USDA soil classification and suitability ratings.

**Data returned:**
- Soil taxonomy (order, suborder, family)
- Typical profile
- Agricultural suitability (for crops, septic, construction)
- Erosion hazard

**External API:** USDA Soil Data Access
- Endpoint: `https://sdmdataaccess.nrcs.usda.gov/`
- Query: Spatial intersection via WebGIS

**Caching:** Redis key `usda:soil:{lat},{lon}`, TTL 30 days

---

### usle.ts

Calculate Universal Soil Loss Equation (USLE) erosion estimates.

**Key Functions:**
- `calculateUSLE(params: USLEParams): ErosionEstimate`

**Purpose:** Estimate annual soil loss due to sheet and rill erosion.

**USLE Factors:**
- R (rainfall-runoff erosivity)
- K (soil erodibility, from soilgrids)
- L (slope length factor)
- S (slope steepness factor)
- C (cover-management factor)
- P (support practice factor)

**Output:** Tons/acre/year erosion estimate

**Caching:** Per-location result cached

---

## Carbon & Climate Services

### carbon-potential.ts

Estimate carbon sequestration potential.

**Key Functions:**
- `getCarbonPotential(lat: number, lon: number, areaHa?: number): Promise<CarbonAnalysis>`

**Purpose:** Calculate carbon storage and sequestration potential for land parcels.

**Factors:**
- Vegetation type (from NLCD)
- Soil carbon (from SoilGrids)
- Climate zone
- Current land use
- Potential reforestation or restoration

**Output:**
- Current carbon stock (tons CO2/ha)
- Annual sequestration rate
- Potential if restored
- Cost-benefit analysis

**External Data:** Soil carbon and vegetation data from integrated services

**Caching:** Redis key `carbon:potential:{lat},{lon}`, TTL 30 days

---

## Strategy & Planning Services

### strategy-scoring.ts

Score environmental strategies and interventions.

**Key Functions:**
- `scoreStrategy(strategy: StrategyInput): StrategyScore`
- `rankStrategies(strategies: StrategyInput[], criteria: ScoringCriteria): RankedStrategies`

**Purpose:** Evaluate and prioritize environmental interventions.

**Scoring factors:**
- Fire risk reduction
- Water quality improvement
- Carbon sequestration potential
- Biodiversity enhancement
- Cost and feasibility
- Community impact

**Output:** Normalized score (0-100) with factor breakdown

**Caching:** Per-strategy result cached

---

### priority-zones.ts

Identify and manage priority intervention zones.

**Key Functions:**
- `identifyPriorityZones(criteria: PriorityZoneCriteria): GeoJSON.FeatureCollection`
- `calculateZoneRanking(zones: Zone[]): RankedZones`

**Purpose:** Automatically identify high-priority areas for intervention.

**Criteria:**
- High fire risk + vegetation stress
- Water scarcity + drought conditions
- Erosion risk + low vegetation cover
- High population impact from hazards

**Output:** Polygon features with priority rank

**Caching:** Redis key `priority:zones:{criteria_hash}`, TTL 24 hours

**Update frequency:** Daily via priority-zone-refresh.ts job

---

## Alert & Notification Services

### alert-engine.ts

Main alert generation and routing engine.

**Key Functions:**
- `checkFireProximityAlerts(userId, lat, lon, radiusKm): Promise<NewAlert[]>`
- `checkWaterAlerts(userId, watchZoneId): Promise<NewAlert[]>`
- `checkDroughtAlerts(userId, monitoredArea): Promise<NewAlert[]>`

**Purpose:** Generate contextual alerts based on user watches, thresholds, and events.

**Alert Severity Levels:**
- INFO: Informational updates (detected near monitored area)
- WARNING: Conditions deteriorating (approaching threshold)
- CRITICAL: Immediate action needed (exceeded threshold)

**Deduplication:** 24-hour window per user/alert-type/location

**Caching:** Alert history in database (not Redis)

---

### email.ts

Email notification service for alerts and digests.

**Key Functions:**
- `sendAlertEmail(userId, alert): Promise<void>`
- `sendDigestEmail(userId, digest): Promise<void>`

**Purpose:** Send email notifications and daily/weekly digests.

**Email Types:**
- Critical alerts (immediate)
- Digest (daily summary at user's preferred time)
- Weekly report (regional stats)

**External Service:** SendGrid or similar SMTP provider
- Env: `SENDGRID_API_KEY` or `SMTP_*` variables

**Rate limiting:** Max 1 email per alert, digest queue managed

---

### geofence.ts

Geofence monitoring and alert triggers.

**Key Functions:**
- `checkGeofenceProximity(assetId, currentLat, currentLon): Promise<GeofenceAlert[]>`

**Purpose:** Monitor asset positions against predefined geographic fences.

**Alert Triggers:**
- On enter (asset enters geofence boundary)
- On exit (asset leaves geofence boundary)
- Dwelling (asset stationary within geofence > X minutes)

**Data:** Geofences stored in PostgreSQL tracking.geofences table

**Caching:** Active geofences cached in Redis

---

## Tracking & Real-Time Services

### tracking.ts

Asset position tracking and historical data.

**Key Functions:**
- `storePosition(assetId, lat, lon, heading, speed): Promise<void>`
- `getTrackHistory(assetId, timeRange): Promise<Position[]>`

**Purpose:** Ingest and query asset position data.

**Data Model:**
- Timestamp (with timezone)
- Asset ID
- Lat/Lon (geography type for PostGIS)
- Heading (0-360 degrees, optional)
- Speed (km/h, optional)
- Altitude (optional)
- Metadata (custom fields)

**Storage:** TimescaleDB hypertable (tracking.positions)
- Automatic time-based partitioning
- Compression for old data

**Retention:** Configurable (default 90 days, then compressed)

---

### realtime.ts

Real-time data streaming via WebSocket and Redis Pub/Sub.

**Key Functions:**
- `publishPositionUpdate(assetId, position): Promise<void>`
- `subscribeToAssetChannel(ws, assetId): void`

**Purpose:** Stream asset positions and alerts to connected clients in real-time.

**Implementation:**
- Redis Pub/Sub for server-to-server messaging
- WebSocket for server-to-client streaming

**Channels:**
- `asset:{assetId}` — Position updates
- `alerts:{userId}` — Alert broadcasts
- `fire:detections` — New fire detections

**Client subscription:** WebSocket `/api/ws?token=...`

---

## AI & Intelligence Services

### ai-prompt.ts

Prepare context and prompts for Claude API integration.

**Key Functions:**
- `assembleRegionalContext(lat, lon): Promise<ContextData>`
- `buildAnalysisPrompt(context, analysisType): string`

**Purpose:** Aggregate environmental data into structured context for AI analysis.

**Context assembly:**
1. Fetch vegetation data (NDVI, biomass)
2. Fetch water status (drought, streamflow)
3. Fetch fire risk (current detections, FWI)
4. Fetch soil data (carbon, health)
5. Fetch historical trends (30/90/365-day comparison)

**Output:** Structured context passed to Claude:

```
Analyze this region for environmental health:

Vegetation:
- NDVI: 0.65 (healthy)
- 30-day trend: +0.05 (improving)
- Biomass: 185 tons/ha

Water:
- Drought index: 2/5 (moderate)
- Streamflow: 45% of historical average
- Precipitation: 15mm (30-day)

Fire:
- Active detections: 3 nearby
- FWI Index: 42 (moderate-high)
- Fuel load: medium

Recommendations:
...
```

**Caching:** Per-region context cached for 1 hour

---

### regional-context.ts

Compile multi-source environmental intelligence.

**Key Functions:**
- `getRegionalStatus(lat, lon, radius?): Promise<RegionalStatus>`

**Purpose:** Single endpoint to get comprehensive regional assessment.

**Aggregates:**
- Fire risk assessment
- Water status
- Vegetation health
- Soil conditions
- Recent events
- Trend analysis

**Output:** JSON object suitable for UI dashboard

**Caching:** Compiled result cached 1 hour

---

## Geocoding & Places Services

### geocoding.ts

Address geocoding and reverse geocoding.

**Key Functions:**
- `geocodeAddress(query: string): Promise<GeocodeResult[]>`
- `reverseGeocode(lat: number, lon: number): Promise<ReverseGeocodeResult>`

**Purpose:** Convert addresses ↔ coordinates.

**External API:** Photon (Nominatim wrapper)
- Endpoint: `PHOTON_URL` environment variable (default `http://localhost:2322`)
- Deployment: Self-hosted Photon instance

**Response format:** GeoJSON FeatureCollection

**Caching:** Redis key `geocode:address:{query}`, TTL 24 hours

---

### places.ts

Place search and discovery.

**Key Functions:**
- `searchPlaces(query: string, bbox?: string): Promise<Place[]>`
- `getPlaceDetails(placeId: string): Promise<PlaceDetail>`

**Purpose:** Search for and get details on places (cities, landmarks, parks, etc).

**Data Source:** Nominatim/OSM community data (via Photon)

**Caching:** Redis key `places:search:{query}`, TTL 24 hours

---

### mapillary.ts

Street-level imagery from Mapillary.

**Key Functions:**
- `searchImageSequences(lat, lon, radius): Promise<ImageSequence[]>`
- `getImageDetails(imageId): Promise<ImageDetail>`

**Purpose:** Retrieve street view imagery for location context.

**External API:** Mapillary API
- Endpoint: `https://api.mapillary.com/v4/`
- Auth: `MAPILLARY_ACCESS_TOKEN`

**Data returned:**
- Sequence ID, captured date
- Image coordinates
- View count

**Caching:** Redis key `mapillary:sequences:{lat},{lon}`, TTL 24 hours

---

## Weather & Climate Services

### weather.ts

Current weather and forecast data.

**Key Functions:**
- `getCurrentWeather(lat: number, lon: number): Promise<WeatherData>`
- `getWeatherForecast(lat: number, lon: number, days?: number): Promise<Forecast>`

**Purpose:** Get weather conditions for fire modeling and alerts.

**Data returned:**
- Temperature (°C)
- Relative humidity (%)
- Wind speed and direction
- Precipitation (mm)
- Cloud cover

**External API:** NOAA or OpenWeatherMap
- Endpoint: Configurable via environment
- Auth: API key

**Caching:** Redis key `weather:{lat},{lon}`, TTL 1 hour

---

## Integration Services

### plantcommerce-api.ts

Integration with PlantCommerce ecosystem.

**Key Functions:**
- `getPrioritySuggestionsForArea(bbox: string): Promise<Suggestion[]>`
- `reportStrategyOutcomes(strategyId, outcomes): Promise<void>`

**Purpose:** Connect PlantGeo analysis with PlantCommerce commerce platform.

**External API:** PlantCommerce API
- Endpoint: `PLANTCOMMERCE_API_URL`
- Auth: HMAC-SHA256 signature with `PLANTCOMMERCE_WEBHOOK_SECRET`

**Data exchanged:**
- Regional priority zones
- Strategy recommendations
- Planting opportunities
- Outcome metrics

**Webhook:** Receives priority zone updates

---

## Layer & Data Services

### layers.ts

Layer management and data serving.

**Key Functions:**
- `getLayerData(layerId): Promise<GeoJSON.FeatureCollection>`
- `updateLayerStyle(layerId, style): Promise<void>`

**Purpose:** Serve layer data and styling to frontend.

**Caching:** Redis key `layer:data:{layerId}`, TTL varies by type

---

## Analytics & Tracking

### analytics.ts

User event tracking and analytics.

**Key Functions:**
- `trackEvent(userId, event, properties): Promise<void>`
- `getEngagementMetrics(startDate, endDate): Promise<Metrics>`

**Purpose:** Track user behavior for analytics dashboards.

**Events tracked:**
- Layer toggled
- Map zoom/pan
- Route calculated
- Feature clicked
- Alert dismissed

**Storage:** PostgreSQL analytics tables (immutable log)

**Caching:** Aggregated metrics cached

---

## Ingest Services

### ingest.ts

Data ingestion pipeline management.

**Key Functions:**
- `validateGeoJSON(data): Promise<ValidationResult>`
- `importFeatures(layerId, features): Promise<ImportResult>`

**Purpose:** Validate and import external geospatial data.

**Validation:**
- GeoJSON schema compliance
- Geometry validity
- Coordinate bounds checking

**Output:** Import status, error log

---

## Job Scheduler Reference

Background jobs defined in `/src/lib/server/jobs/`:

| Job | Trigger | Frequency |
|-----|---------|-----------|
| `alert-dispatcher.ts` | New alert event | Immediate |
| `email-digest.ts` | User schedule | Daily/Weekly |
| `priority-zone-refresh.ts` | System trigger | Daily |
| `priority-zone-webhook.ts` | PlantCommerce event | On webhook |
| `water-refresh.ts` | System trigger | Hourly |
| `conversation-cleanup.ts` | System trigger | Weekly |

---

## Environment Variables by Service

| Service | Env Variables |
|---------|---------------|
| NASA FIRMS | `NASA_FIRMS_KEY` |
| Mapillary | `MAPILLARY_ACCESS_TOKEN` |
| USDA/USGS | No auth (public) |
| ISRIC SoilGrids | No auth (public) |
| Photon/Nominatim | `PHOTON_URL` |
| OpenWeatherMap | `OPENWEATHER_KEY` |
| PlantCommerce | `PLANTCOMMERCE_API_URL`, `PLANTCOMMERCE_WEBHOOK_SECRET` |
| Anthropic Claude | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| SendGrid | `SENDGRID_API_KEY` |

---

## Service Dependencies

```
alert-engine
  ├→ nasa-firms (fire detections)
  ├→ usgs-water (water data)
  ├→ drought (drought status)
  └→ email (notifications)

strategy-scoring
  ├→ fire-risk
  ├→ vegetation
  ├→ carbon-potential
  └→ geofence

regional-context
  ├→ vegetation
  ├→ usgs-water
  ├→ fire-risk
  ├→ soilgrids
  └→ carbon-potential

ai-prompt
  └→ regional-context

realtime
  └→ Redis Pub/Sub
```

## Performance & Caching Strategy

Most services implement multi-level caching:

1. **In-memory** (function-level): Small, fast data
2. **Redis** (shared): Medium-sized computations, 1hr-30day TTL
3. **Database** (persistent): Historical data, alerts, transactions

Services check Redis first, then fall back to external API or computation, then cache result.
