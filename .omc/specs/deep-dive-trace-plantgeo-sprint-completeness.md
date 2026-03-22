# Deep Dive Trace: plantgeo-sprint-completeness

## Observed Result
PlantGeo Sprints 8-10 (Tracks 21-30) produced 10 new tracks of environmental data, community features, and platform integrations. The question: are these implementations production-ready, properly integrated, and does the codebase support a new AI Regional Intelligence feature?

## Ranked Hypotheses
| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | New panels/layers are built but never mounted — users cannot reach them | High | Strong | render-tree trace confirmed: page.tsx has no sidePanel prop, AlertBell has zero import sites, all layer components have zero consumers |
| 2 | Multiple services use hardcoded/mock values that silently produce wrong output | High | Strong | percentile=null always, rainfall=600 everywhere, 4/5 strategy factors are lat/lon trig mocks, reforestation zones are mock rectangles |
| 3 | Only 1 of 5 BullMQ jobs is started; bullmq not in package.json | High | Strong | instrumentation.ts imports only priority-zone-refresh; bullmq absent from dependencies |
| 4 | Direct context injection (no vector DB) is the right AI RAG architecture | High | Strong | all data services exist, token estimate ~1,420-2,500 tokens, SSE infrastructure reusable |

## Evidence Summary by Hypothesis
- **H1 (UI wiring)**: page.tsx passes no sidePanel prop to MapLayout. AlertBell has zero import sites outside its definition. Grep for all 8 new panel names in src/app/ returns zero files. All new map layer components have zero consumers.
- **H2 (mock values)**: usgs-water.ts line 111: `const percentile = null` always. carbon-potential.ts line 113: `const rainfall = 600` always. strategy-scoring.ts lines 201-223: 4 factors use sinusoidal lat/lon math with explicit TODO(Track 21-25) markers. environmental router getReforestationZones returns 3 hardcoded rectangles.
- **H3 (jobs not started)**: instrumentation.ts lines 1-6: only startJobs from priority-zone-refresh imported. bullmq absent from package.json — lazy require silently swallows MODULE_NOT_FOUND.
- **H4 (AI architecture)**: location-context API already aggregates fire+soil+drought+NDVI+zones. SSE streaming at /api/stream/ uses ReadableStream + Redis pub/sub. Token estimate for 5 core data points: ~1,420 tokens. All structured, point-queryable data — no semantic search needed.

## Evidence Against / Missing Evidence
- **H1**: AlertPanel IS self-contained and would work if AlertBell were mounted. Layer components are fully implemented — gap is purely at mount point.
- **H2**: FWI calculations in fire-weather-index.ts are real Van Wagner 1987 equations. SoilGrids calls are real. MTBS Redis caching is correct.
- **H3**: All 5 job files are structurally complete. priority-zone-refresh is the model for the others.
- **H4**: NDVI has no point-value query (only tile URLs). @anthropic-ai/sdk not installed. strategy-scoring mocks must be replaced before AI output is trustworthy.

## Per-Lane Critical Unknowns
- **Lane 1 (Implementation)**: Is bullmq intentionally absent (deferred dep) or accidentally omitted? Discriminating probe: `SELECT COUNT(*) FILTER (WHERE percentile IS NOT NULL) FROM water_gauges;` — if 0, confirms both null-percentile and job-never-ran defects compound.
- **Lane 2 (Integration wiring)**: Are panels intentionally deferred for a future sprint, or accidentally omitted? Discriminating probe: `grep -r "sidePanel\|<SidePanel" src/ --include="*.tsx"` — zero results confirms structural omission, not accidental deletion.
- **Lane 3 (AI RAG)**: Can a numeric NDVI value be obtained at a clicked lat/lon? Discriminating probe: call NASA GIBS WMS GetFeatureInfo for a known coordinate — if supported, NDVI gap closes with one new function; if not, NASA AppEEARS API (requires Earthdata key) is needed.

## Rebuttal Round
- Best rebuttal to leader (H1): panels could be feature-flagged or auth-gated in an unread page. **Why leader held**: direct read of dashboard/page.tsx and full grep of src/app/ returned zero panel import sites. No conditional path exists.
- Best rebuttal to H4 (AI direct injection): strategy scoring 4/5 factors are mocks — AI output would be fabricated. **Why H4 held**: mock values are a data-completeness problem, not architecture. TODOs point to real tracks. Direct injection remains correct; confidence levels surface the uncertainty.

## Convergence / Separation Notes
- H2 (mocks) and H3 (jobs not started) partially converge on water data: even when the water refresh job runs, it writes null percentile to DB because the NWIS stats-service step was never built.
- H1 (UI wiring) and H3 (jobs) are independently rooted (React tree vs server-side instrumentation).
- H4 (AI architecture) is fully independent and positive — the architecture is sound.

## Most Likely Explanation
The codebase is in a scaffolded-but-incomplete state for Tracks 21-30: correct formulas and real API clients exist throughout, but integration seams contain confirmed hardcoded placeholders, a broken job orchestration entry point (1 of 5 jobs started, bullmq absent), and zero UI mount points for 8 new panels and 9 new map layers. The tRPC routers are the one correctly wired layer. The AI RAG architecture is feasible and well-supported by existing infrastructure.

## Critical Unknown
Whether NDVI numeric point-value retrieval is available from NASA GIBS WMS GetFeatureInfo — this determines whether the AI context can include actual vegetation health numbers or only references the tile URL.

## Recommended Discriminating Probe
`curl "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetFeatureInfo&LAYERS=MODIS_Terra_NDVI_M&QUERY_LAYERS=MODIS_Terra_NDVI_M&INFO_FORMAT=application/json&I=128&J=128&WIDTH=256&HEIGHT=256&BBOX=-121,37,-119,39&CRS=EPSG:4326"` — if returns pixel value, NDVI gap is closed with a single new service function.

## Fixes Applied (Post-Trace)
- ✅ CRITICAL: XSS via unsanitized popup HTML in WaterLayer.tsx — escapeHtml() applied
- ✅ CRITICAL: Unreachable groundwater "critical" trend branch — order reversed
- ✅ HIGH: Duplicate Redis singletons — consolidated to shared getRedis()
- ✅ HIGH: NDVI anomaly/absolute modes return identical URLs — fixed
- ✅ HIGH: contributorProcedure null session assertion — explicit guard added
- ✅ HIGH: Vote count race condition — .returning() check added
- ✅ MEDIUM: "use client" not first statement in AnalyticsDashboard — fixed
- ✅ MEDIUM: bbox inputs unvalidated — bboxSchema regex added
- ✅ INTEGRATION: All 8 panels wired via PanelManager.tsx
- ✅ INTEGRATION: AlertBell mounted in MapView.tsx
- ✅ INTEGRATION: New map layers mounted via LayerManager.tsx
- ✅ INTEGRATION: instrumentation.ts now starts all 5 BullMQ jobs
- ✅ INTEGRATION: bullmq added to package.json
- ✅ DATA: strategy-scoring.ts waterStress now uses real drought + USGS data
