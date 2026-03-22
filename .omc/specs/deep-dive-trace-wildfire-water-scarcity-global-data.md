# Deep Dive Trace: wildfire-water-scarcity-global-data

## Observed Result
PlantGeo has completed 20 implementation tracks but the question is: does it fulfil the knowledge base vision of an **interactive regional/global data visualization tool for proactively responding to wildfire risks, water scarcity, and other environmental crises** — with market-force incentivization to deploy resources?

## Ranked Hypotheses
| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | Market mechanics & incentivization gap — zero economic layer (carbon credits, MRV, DAO, marketplace) | **High** | **Strong** | Definitive absence: not a partial implementation but zero economic code in the entire codebase |
| 2 | Environmental data coverage gap — missing soil, water, vegetation/satellite, biodiversity, climate baseline layers | **High** | **Strong** | 60% of required data sources missing; fire risk model uses hardcoded vegetation weights instead of real LANDFIRE data |
| 3 | Architecture & scale readiness gap — vector-only architecture not ready for global raster datasets, Sentinel-2 ingestion, predictive models | **High** | **Strong** | Martin tile server configured vector-only; no COG/STAC infrastructure; Railway Pro deployment undersized for TB-scale satellite data |

## Evidence Summary by Hypothesis

### Hypothesis 1 — Market Mechanics Gap
- `src/lib/server/db/schema.ts`: 15 tables — zero economic tables (no carbon_credits, transactions, tokens, treasury, marketplace, MRV)
- `src/lib/server/trpc/routers/`: 9 routers — zero economic routers (no marketplace, carbon accounting, DAO voting, revenue distribution)
- `src/components/panels/EcosystemTracker.tsx`: Logs ecosystem actions (reforestation, invasive removal) but zero carbon quantification, zero credit generation, zero incentive tracking
- `conductor/tracks/01-20`: None address economic layer; Track 10 mentions "impact metrics" but measures only actions, not monetization
- Knowledge base `09_flywheel_ecosystem_model/FLYWHEEL_MODEL.md`: Full flywheel described (Carbon Credits → Revenue → Incentives → Participation → Scale) — fully documented but not implemented
- `plantcommerce/plantapp/src/lib/server/db/schema.ts`: Has product sales/affiliate commissions — but this is a separate project with **zero integration** with plantgeo

### Hypothesis 2 — Environmental Data Coverage Gap
- `src/lib/server/db/schema.ts`: Only `fireDetections` table for environmental data; no soil, water, vegetation, biodiversity tables
- `src/lib/server/services/`: 13 services, only `weather.ts` + `fire-risk.ts` handle environmental data
- `src/components/map/layers/`: 16 layers, only `WeatherLayer` + `FireRiskLayer` render environmental data
- `src/lib/server/services/fire-risk.ts`: Uses hardcoded vegetation weights (chaparral=0.95, grassland=0.8) instead of LANDFIRE fuel model data
- Knowledge base `08_data_sources/DATA_SOURCES_AND_TOOLS.md`: Specifies USDA Web Soil Survey, SoilGrids, LANDFIRE, Sentinel-2/NDVI, GBIF, WorldClim, USGS NWIS, PRISM — all absent
- **Counterpoint**: Generic `layers`/`features` schema uses JSONB, making new data types easy to add without migrations

### Hypothesis 3 — Architecture & Scale Gap
- `infra/martin/martin.yaml`: Configured for vector tiles only (pmtiles, mbtiles, functions); no COG raster paths
- `src/lib/map/sources.ts:11-12,35-41`: Only 2 hardcoded raster sources (AWS Terrain, ArcGIS satellite); no Sentinel-2 or dynamic raster infrastructure
- `infra/db/init/01-extensions.sql:51-52`: TimescaleDB hypertable exists but only for `tracking.positions`; `fireDetections` is NOT a hypertable; no continuous aggregates
- `conductor/product.md:34-41`: Railway Pro allocated 8GB PostGIS RAM — inadequate for TB-scale spatial indexes
- `src/lib/server/services/ingest.ts`: Synchronous `Promise.all()` ingestion; no batch pipeline, queuing, or backpressure
- **Counterpoint**: Martin v1.4 CAN serve rasters via PostGIS; deck.gl v9 has BitmapLayer for raster; TimescaleDB CAN scale — but none of these capabilities are configured or integrated

## Evidence Against / Missing Evidence

### Against Hypothesis 1
- Teams/roles system could evolve toward DAO-like governance (but would need complete rebuild)
- API keys system is a foundation for partner access (but not token economics)
- `contributions.ts` router has expert review workflow (governance of data, not economic governance)

### Against Hypothesis 2
- Architecture is extensible — pluggable routers, JSONB schema, replicable ingest pattern make additions low-friction
- All required data sources are free/open (no licensing barriers)
- Existing `ingest.ts` pattern is replicable for new data types

### Against Hypothesis 3
- Martin v1.4 supports raster via PostGIS functions (not blocked architecturally)
- PostGIS raster extension available (not used, but possible)
- Deck.gl v9 BitmapLayer for raster visualization exists
- TimescaleDB supports continuous aggregates (not configured, but supported)

## Per-Lane Critical Unknowns

- **Lane 1 (Market mechanics)**: Was the economic layer intentionally scoped out of the MVP (plantgeo = geospatial foundation; plantcommerce = economic layer), or is it an architectural oversight with no planned integration path?
- **Lane 2 (Environmental data)**: Are there any partially-implemented environmental data services under alternative naming (terrain, moisture, ecosystem, biome) that weren't found by initial grep?
- **Lane 3 (Architecture/scale)**: Does the vision require ingesting raw Sentinel-2 satellite imagery (petabyte-scale, requiring COG/STAC infrastructure) or only derived indices (NDVI/NBR as computed GeoTIFFs — kilobyte-scale, manageable with current architecture)?

## Rebuttal Round
- **Best rebuttal to leader (Hypothesis 1)**: The plantcommerce repo has a separate economic system (product sales, affiliate commissions). Perhaps the intended architecture is two separate applications — plantgeo handles the geospatial layer while plantcommerce handles the economic layer — and integration is planned but not yet built.
- **Why Hypothesis 1 holds**: Zero integration code exists between plantgeo and plantcommerce. No shared DB, no shared API calls, no shared auth tokens. They are completely independent systems. The knowledge base describes them as one unified platform ("affiliate tool"), not two separate systems.

## Convergence / Separation Notes
- **Lanes 1 and 2 partially converge**: Both point to the same root cause — PlantGeo was built as a **geospatial visualization MVP focused on wildfire prevention**, not the full regenerative agriculture platform the knowledge base envisions. The gap is scope, not architecture.
- **Lane 3 is distinct**: The scale/architecture gap is an independent constraint that will matter once features are added. Whether raw satellite or derived indices determines the magnitude of infrastructure work.
- **The three gaps compound**: Without environmental data (Lane 2), you can't calculate carbon sequestration. Without carbon accounting, you can't issue credits. Without credits, the economic flywheel (Lane 1) can't function. Lanes 1 and 2 must be addressed sequentially, not in parallel.

## Most Likely Explanation
PlantGeo has successfully built a **strong geospatial foundation** (MVP) covering wildfire detection, intervention mapping, 3D visualization, real-time tracking, and team collaboration — but covers approximately **30% of the knowledge base vision**. The remaining 70% spans: (a) foundational environmental data layers required before impact quantification is possible (soil, water, vegetation, climate — Lane 2), (b) the economic incentivization flywheel that transforms environmental action into community economic participation (carbon credits, MRV verification, DAO governance, marketplace — Lane 1), and (c) the architectural readiness for global-scale satellite data and predictive models (COG infrastructure, STAC catalog, batch pipelines — Lane 3). The three gaps compound: data → accounting → incentives is a sequential dependency chain.

## Critical Unknown
**The single most important question**: Is PlantGeo intentionally the geospatial layer of a two-system architecture (plantgeo + plantcommerce), or should it become the unified platform? This determines whether to build the economic layer INTO plantgeo or to build an integration bridge between the two existing codebases.

## Recommended Discriminating Probe
**Ask the project owner**: "Should carbon accounting, credit issuance, DAO governance, and the marketplace live in PlantGeo or in PlantCommerce — and what is the integration architecture between them?" The answer determines the entire development roadmap: either expand plantgeo's scope significantly (merge platforms) or define the API contract between plantgeo (geospatial) and plantcommerce (economic) and build the bridge.
