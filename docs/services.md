# Backend Services Reference

> **STATUS — 2026-08-22, body below untouched.** The architecture pivot
> (`conductor/RUNBOOK.md` §0.23/§0.24) moves every data plane out of Postgres
> into day-partitioned Parquet on Railway object storage, read by DuckDB+Polars;
> Postgres is retained for community features only. Several services listed below
> read or write the Postgres planes §0.24.1's stream table (`geo.features`,
> `geo.drought_areas`, `agri.signal_observation`, `agri.forecast_observation`)
> targets for migration — verify a service's storage backend against that table
> before relying on this reference. Read RUNBOOK §0.23/§0.24 first.

PlantGeo includes 30+ backend services that integrate with external APIs, perform geospatial computations, and manage system state. All services are located in `/src/lib/server/services/`.

## Fire Detection & Analysis Services

### nasa-firms.ts (removed 2026-09-18)

Deleted. `fetchActiveFiresNASA` had no callers in `src/`; live fire detections are served
by the governed Parquet lane (`getParquetFireDetections` in `parquet-trpc-readers/fire-detections.ts`).

---

### fire-risk.ts (removed 2026-09-18)

Deleted. `calculateFireRisk` had no callers in `src/`.

---

### fire-weather-index.ts (removed 2026-09-18)

Deleted. `calculateFullFWI` and the component functions (`calculateFFMC`, `calculateDMC`,
`calculateDC`, `calculateISI`, `calculateBUI`, `calculateFWI`) had no callers in `src/`.

---

### landfire.ts (removed 2026-09-15)

Deleted. `getLandFireEVT` had no callers, its ArcGIS host had moved (HTTP 404), and it mapped FBFM40 fuel-model codes against Existing Vegetation Type values, so it never returned a real classification. Vegetation type is being designed as a governed Parquet lane (`.omc/research/runbook-20260915-vegetation-type/PLAN.md`); nothing serves it today.

---

### Governed MTBS burn history

`getParquetBurnSeverity` in `parquet-trpc-readers.ts` serves both the map and
regional context at the selected day. Historical cohort publications are unioned;
an eligible governed current snapshot replaces its entire declared fire-year scope.
Capture availability, source capture time, and partial mapping years remain separate
metadata, including for an empty viewport or a captured zero-fire replacement.
The retired direct ArcGIS/Redis reader and its exclusively owned types have no runtime
consumers; the public unpublished-risk procedure is retained independently.

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

### drought.ts (removed 2026-09-18)

Deleted. `getDroughtClassification`/`getDroughtByDate` had no callers in `src/` — the same
name is a `getParquetDroughtClassification`-backed tRPC procedure with no dependency on this
module. It also fetched an upstream directly, against services/AGENTS.md's "governed Parquet
readers only" boundary, which was a second reason to retire rather than repair it.

---

## Vegetation & Land Cover Services

### vegetation.ts (removed 2026-09-18)

Deleted. This was an 11-line pure re-export of `@/lib/vegetation` with no importers in `src/`.

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

### usle.ts (removed 2026-09-18)

Deleted. `calculateErosionRisk`/`classifyErosionRisk` had no callers in `src/`.

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

### geofence.ts (removed 2026-09-18)

Deleted. `checkGeofences` had no callers in `src/`.

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

**Storage:** PostgreSQL table (tracking.positions; formerly a TimescaleDB hypertable until 2026-08-25, when the extension was removed)
- Time-ordered append-only log
- Manual partitioning via PostgreSQL range partitions if needed

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

### plantcommerce-api.ts (inactive)

The partner supplier directory is deliberately disabled. PlantGeo does not
forward selected coordinates, priority zones, or opportunity data to an
external commerce service while the system lacks a reviewed directory release,
explicit outbound-location consent, and paid account or partner entitlement.

**Current behavior:** `getStrategySuppliers` fails closed; legacy priority-zone
webhooks send nothing. A future integration needs a private partner contract,
durable audit/usage controls, and a reviewed publication boundary before it
may make an outbound request.

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
| Mapillary | `MAPILLARY_ACCESS_TOKEN` |
| USDA/USGS | No auth (public) |
| ISRIC SoilGrids | No auth (public) |
| Photon/Nominatim | `PHOTON_URL` |
| OpenWeatherMap | `OPENWEATHER_KEY` |
| PlantCommerce | `PLANTCOMMERCE_API_URL`, `PLANTCOMMERCE_WEBHOOK_SECRET` |
| OpenRouter (google/gemini-2.5-flash-lite) | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL` |
| SendGrid | `SENDGRID_API_KEY` |

---

## Service Dependencies

```
alert-engine
  ├→ usgs-water (water data)
  └→ email (notifications)

strategy-scoring
  └→ carbon-potential

regional-context
  ├→ usgs-water
  ├→ soilgrids
  └→ carbon-potential

ai-prompt
  └→ regional-context

realtime
  └→ Redis Pub/Sub
```

(`nasa-firms.ts`, `drought.ts`, `fire-risk.ts`, `vegetation.ts`, and `geofence.ts` were removed
2026-09-18 — see the per-service notes above.)

## Performance & Caching Strategy

Most services implement multi-level caching:

1. **In-memory** (function-level): Small, fast data
2. **Redis** (shared): Medium-sized computations, 1hr-30day TTL
3. **Database** (persistent): Historical data, alerts, transactions

Services check Redis first, then fall back to external API or computation, then cache result.
