# Data Readiness & Usability Audit — 2026-08-02

**Method:** static code, migration, and config reading. **No live database was queried** (the auditing lane had no shell access, and there is no `.env` at the repo root — only `.env.example`). Every "rows present" judgement below is therefore **UNVERIFIED**, not asserted. Restoring a `DATABASE_URL` and running a dozen read-only `COUNT`/`MIN`/`MAX` queries would close that column.

**Correction applied post-audit:** the original pass claimed `services/agri-data-service/alembic/versions/` contained only `.gitkeep`. That is false — 14 migrations (`0001`–`0014`) are committed and tracked. The surviving, verified finding for that row is the narrower one: the agri service has **zero references from `src/`**.

---

## Verdict table

| # | Layer (UI promise) | Upstream + cadence | Ingestion exists? | Coverage: actual vs implied | Fabricated / placeholder | Verdict — blocking gap |
|---|---|---|---|---|---|---|
| 1 | **Fire detections** — "published FIRMS detections, last 24h" (`FireDashboard.tsx:66,90-92`) | NASA FIRMS, cron 30 min (`.github/workflows/ingest-cron.yml:5`) | Yes → `geo.features` (`ingestion-jobs.ts:87-136`) | `INGEST_BBOX` only, capped 30°×20° (`ingestion-jobs.ts:40,56-57`); job returns `skipped` if unset. UI is a globe | Label says "last 24h"; `firmsDayRange()` defaults to **2 days** (`environmental-time.ts:66-70`) | **PARTIAL** — unset bbox ⇒ zero rows; one metro sold as global |
| 2 | **Streamflow gauges** (`LayerManager.tsx:65-68`) | USGS NWIS, 30 min | Yes (`ingestion-jobs.ts:139-176`) | Same single bbox | Read model silently drops gauges older than 6 h (`environmental-read-model.ts:13,157`) — empty rather than stale-labelled | **PARTIAL** — two competing water stores; the one analytics reads (`water_gauges`) is fed by a job off by default (`instrumentation.ts:5-11`) |
| 3 | **Groundwater wells** (`LayerManager.tsx:69-72`) | none | **No** | n/a | `getPublishedGroundwaterWells` validates the bbox then `return []` (`environmental-read-model.ts:250-255`) | **NOT-READY** — facade |
| 4 | **Drought (USDM)** (`LayerManager.tsx:59-63,126`) | USDM, weekly | **No writer.** `services/drought.ts` is imported by nothing in `src/`; `drought_data` has no producer | none | Router returns a hardcoded empty collection (`environmental.ts:132-134`) while a working reader `getPublishedDroughtClassification` (`environmental-read-model.ts:191`) sits **uncalled** | **NOT-READY** — no job; router bypasses its own read model |
| 5 | **Vegetation NDVI/NDWI/NBR** (`VegetationPanel`) | claimed GIBS/Copernicus | Ingest job returns `skipped` unconditionally (`ingestion-jobs.ts:291-299`) | none | `getEnvironmentalTileTemplate()` returns `""` for everything (`vegetation.ts:15-17`); `sources.ts:76-87` ships `tiles: [""]` | **NOT-READY** — facade with full legends and sliders |
| 6 | **Soil properties** (`SoilPanel`) | claimed SoilGrids/ISRIC | none | none | `getSoilProperties()` always throws (`soilgrids.ts:36`); `soil_grid_cache` has no writer | **NOT-READY** — facade **and** dead wiring: `PanelManager.tsx:130-133` never passes `queryPoint`, so the panel's queries are `enabled:false` and never fire |
| 7 | **Weather** — **default-ON** (`map-store.ts:40`) | Open-Meteo, 30 min | Yes, writes `weather-observations` (`ingestion-jobs.ts:197-239`) | `INGEST_BBOX` | Read side is a hard stub: `getWeatherForPoint` → `unpublishedRisk()` → always throws (`wildfire.ts:148-155`) | **NOT-READY** — data written, no read path; a default-on toggle that can never render |
| 8 | **Fire risk zones / perimeters** (`FireDashboard.tsx:67`) | WFIGS `_Current`, 30 min | Yes (`ingestion-jobs.ts:242-288`) | `INGEST_BBOX`; `minzoom:4` implies continental | (a) MVT selects `severity`/`risk_level` (`0001_handy_riptide.sql:395-396`); ingester writes **neither** ⇒ every polygon is the `#fbbf24` fallback (`layers.ts:75`). (b) insert-only, skip-if-exists (`ingest.ts:66-69`) ⇒ perimeters **never update**; `percentContained` frozen at first sighting | **PARTIAL** — permanently stale; severity styling fabricated |
| 9 | **Sensor network** (`FireDashboard.tsx:68`) | push-only `POST /api/ingest/sensors` | Route exists, **no producer** | none | MVT selects `status`/`sensor_type`/`name`; route writes only `sensor_id`/`timestamp`/`readings` ⇒ always the `#6b7280` default (`layers.ts:113`) | **NOT-READY** — no producer + property contract mismatch |
| 10 | **Interventions** (`CommunityPanel.tsx:111`) | user-created | user-generated only | n/a | Writes `strategyId/name/priority`; MVT selects `intervention_type`/`status` ⇒ always `#6d28d9` (`layers.ts:136`). `geometry` optional ⇒ geom trigger nulls it and the feature never tiles | **PARTIAL** |
| 11 | **3D building footprints** (`MapControls.tsx:88`) | OSM, manual one-shot import | manual | whatever was imported | **Source-layer name wrong**: `layers.ts:162` says `"geo.building_tiles"`, the function emits `'buildings'` (`0001:499`) ⇒ can never render | **NOT-READY** — one-line bug |
| 12 | **OSM roads / waterways** — always-on, no toggle | manual import | manual | — | `layers.ts:177,189` reference source-layers Martin never publishes — `auto_publish: false`, only 4 functions exposed (`infra/martin/martin.yaml:24-37`) | **NOT-READY** — silently blank, no error surface |
| 13 | **Demand heatmap** (`LayerManager.tsx:143-148`) | partner "action network" | none | none | `/api/v1/action-network` returns a hardcoded 503 (`route.ts:8-24`) — but the UI **says so** in a banner (`DemandHeatmapLayer.tsx:154-156`) | **NOT-READY** — facade, honestly self-declaring |
| 14 | **Basemap / terrain / satellite** | Protomaps daily, AWS terrarium, Esri | external | global | Default archive pinned to `20260801.pmtiles` (`sources.ts:12`), which Protomaps prunes; the repo documents that this 404s and blanks the map | **PARTIAL** — works until the pin expires; needs `NEXT_PUBLIC_PMTILES_URL` |
| 15 | **Analytics dashboard** | derived | — | — | `db/analytics.ts` reads `geo.fire_detections`, which **nothing writes** (all ingestion targets `geo.features`); filters on property keys the FIRMS ingester never emits; substitutes 0 for unknown (`:121`), 0 ⇒ "no drought" (`:151`), and the two zeros drive an unconditional `"improving"` trend (`:163-168`) | **NOT-READY but honestly gated** — the router throws `PRECONDITION_FAILED` for all three procedures, so this is latent dead code. Delete or gate before anyone rewires it |
| 16 | **Strategy / suitability / carbon** | — | none | — | Fixed `availability:"unavailable"` objects (`carbon-potential.ts:31-47`, `strategy-scoring.ts:37-57`) | **NOT-READY** — fail-closed by design |
| 17 | **agri-data-service** | ERA5, USDM, etc. | Substantial Python service, real routes, **14 committed Alembic migrations** | — | — | **NOT-READY for the product** — grep for `AGRI_`/`agri-data-service`/`:8000` across `src/` returns no matches. The service is sound; it is simply not wired to the frontend |

---

## Summary

**Demoable today** (given `INGEST_BBOX`, `CRON_SECRET`, and a reachable Postgres): fire detections and streamflow gauges — the only two planes that put real current bytes on screen — plus the third-party basemap/terrain/satellite.

**Facades** — a control surface exists but no data can reach it: vegetation, soil, weather, groundwater, drought, demand heatmap, building footprints, OSM roads/waterways, sensors, strategy/carbon, and the agri service. **11 of 17 planes.** Several are default-on or prominently toggleable, so a demo hands the user a switch that provably does nothing.

### Highest-severity findings

1. `vegetation.ts:15-17` — one function returning `""` silently voids four UI features (NDVI, NDWI, NBR, soil).
2. `db/analytics.ts:121,151,163-168` — zero-for-unknown substitution yields an unconditional "improving" risk trend. Unreachable today; a loaded gun.
3. `layers.ts:75,113,136` — default colors stand in for `severity`/`status`/`intervention_type` that no ingester writes. The map *looks* like it encodes real severity; every feature is the fallback color.
4. `ingest.ts:66-69` + `ingestion-jobs.ts:263` — insert-only ingestion freezes fire perimeters at first observation, forever.
5. `sources.ts:76-87` — `tiles: [""]` shipped in the base style object.
6. `environmental-read-model.ts:250-255` — a function that validates its input, then returns `[]`.

### Cheapest PARTIAL → READY conversions, in order

1. **Drought** — highest value per line in the repo. `getPublishedDroughtClassification` is already written; point `environmental.ts:132-134` at it and add an ingestion job wrapping the existing `services/drought.ts:13`.
2. **Building footprints** — one line: `layers.ts:162`, `"geo.building_tiles"` → `"buildings"`.
3. **Weather** — the ingest job already writes the data. Replace the `unpublishedRisk()` stub at `wildfire.ts:155` with a read model mirroring `getPublishedStreamflowGauges`. Until then, drop `"weather"` from `map-store.ts:40` so the demo stops shipping a dead default-on toggle.
4. **Fire perimeters** — versioned `featureId` or an upsert path, plus emit `severity` from `percentContained` so styling stops being a constant.
5. **Sensors / interventions** — align written property keys with what the MVT functions select.
6. **OSM roads/waterways** — publish them in `martin.yaml` or remove the layers; they are always-on and always blank.
7. **Soil panel** — pass `queryPoint` from `PanelManager.tsx:130-133` so it at least attempts its query.
8. **Delete or gate `db/analytics.ts:95-177`.**
9. **Coverage honesty** — every `INGEST_BBOX`-fed layer is one ≤30°×20° region beneath a global map. Surface the ingested bbox in the UI so panning to Europe doesn't read as "no fires."

### Not verified

Row counts, timestamp ranges, and staleness for every table; whether `geo.layers` seed rows survive in any live DB; whether the OSM tables were ever imported; whether Martin is running; whether the agri schema is materialized anywhere.

---

## Live database census (2026-08-02, added post-audit)

The original audit was static-read-only and every row-count claim was marked
UNVERIFIED. Those claims are now resolved against the live database.

**Connection.** `DATABASE_URL` *is* configured — in `.env.local` (not `.env`),
pointing at the Railway instance `crossover.proxy.rlwy.net:14115/railway`. The
earlier note that "no DATABASE_URL exists, so the DB is unreachable" was wrong.

**Result: the database is bare.**

| Check | Result |
| --- | --- |
| Schemas present | `public`, `drizzle`, `information_schema` only — **no `geo` schema** |
| Base tables in `geo`/`public` | 0 |
| `drizzle.__drizzle_migrations` rows | 0 |

No migration has ever been applied to this database. There is no `geo.features`,
no `water_gauges`, no `drought_data`, no `geo.osm_buildings`.

**What this changes.** The facade-vs-working distinction in this audit is a
*code-level* distinction only. At the data level every one of the 17 planes is
empty, including fire detections and streamflow gauges — the two the audit called
end-to-end. Those two have a complete read path and a real producer; they simply
have nothing to read yet. `INGEST_BBOX` is therefore not the binding constraint:
even a correctly-set bbox would fail, because the ingestion target tables do not
exist. Running the migrations is a strict prerequisite to any data-readiness
claim, and to re-running this census meaningfully.

**Caveat.** This is one deployment target. A different environment may hold data;
nothing here was checked beyond the `DATABASE_URL` in `.env.local`. No writes
were performed — all queries were read-only catalog inspection.
