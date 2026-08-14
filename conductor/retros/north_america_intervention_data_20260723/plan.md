---
type: implementation-plan
---

# Implementation plan

## Current state — reviewed 2026-07-26

The completed phases below are historical pilot evidence. Their references to a
warehouse at revision `0007` describe that pilot snapshot, not the current local
schema head (`20260725_0013`). Further expansion is planned only after each
source adapter passes its specific licence, coverage, resolution, and release
lineage gate; it cannot authorize a forecast, strategy recommendation, Railway
mutation, or schedule.

## Phase 0 — preserve and baseline

- [x] Read root and applicable database/model/execution/warehouse instructions.
- [x] Record the dirty checkout; do not reset, stage broadly, publish, deploy,
  schedule, or mutate protected forecast/strategy areas.
- [x] Confirm the dedicated local warehouse is PostgreSQL 16/PostGIS and retains
  the existing NASA POWER/ERA5-Land/USDM lineage.
- [x] Create and catalog/checksum-verify a post-receipt local backup, then
  restore it to the guarded disposable `plantgeo_geospatial_test_*` database.
  The earlier pre-hindcast backup remains evidence only.
- [x] Keep the persistent warehouse at `0007`; rehearse `0008` and `0009` only
  in that disposable restored clone. No persistent migration is authorized.

## Phase 1 — source governance and coverage

- [x] Build the US/Canada/Mexico source/coverage/resolution/licence matrix.
- [x] Define inference-scale gates and explicit `unknown`/blocked states.
- [x] Select Boise and the ODbL Hillside to Hollow Reserve boundary; classify it
  as a named property with neighborhood support, not a legal parcel.
- [x] Convert the register into machine-readable source-adapter manifests as
  each adapter is implemented.

## Phase 2 — minimal raw capture

- [x] Add a bounded HTTPS capture command with domain allowlisting, retry/backoff,
  maximum-byte limits, user-agent identification, recorded HTTP validators,
  immutable-cache reuse, atomic writes, SHA-256 receipts, and no access-control
  bypass.
- [x] Keep only the reviewed open-source plan in version control; raw bytes and
  receipts remain in ignored content-addressed local storage.
- [x] Capture Census Boise, versioned OSM way `674700373`, and the complete
  pinned USFS 2020 WUI AOI response selected by the property bounding box.
- [x] Verify counts, feature identity, CRS, geometry type/validity, checksums,
  and licence snapshots before any warehouse write.

## Phase 3 — additive PostGIS evidence plane

- [x] Add forward-only Alembic/ORM objects for normalized source features,
  stable subjects, evidence inputs, and relational lineage.
- [x] Enforce SRID 4326, geometry validity, immutable source identity, source
  support kinds, native resolution/scale, maximum inference scale, confidence,
  evidence type, and `life_safety_validated = false`.
- [x] Implement a local-only idempotent writer that pins all pilot releases in
  one validated release set and never advances a publication pointer.
- [x] Compute versioned PostGIS features: city/property geodesic area, property
  contained by city, WUI intersection/fraction/class, and the explicitly
  conservative WUI-vintage minimum-age lower bound.
- [x] Preserve but exclude existing USDM history because an explicit
  redistribution licence was not verified; record current drought as a blocked
  regional gap until open terms or an alternative open source are archived.

## Phase 4 — mitigation-input coverage, not recommendations

- [x] Wildfire: WUI designation, terrain/fuels/building/egress/fire-history
  evidence requirements, with missing inspection/structure attributes as gaps.
- [x] Drought/watershed: watershed/hydro/wetland/soil/groundwater/weather
  context, plus missing infiltration, water-right, drainage, and field tests.
- [x] Aquaponics/hydroponics: legal water source, water-quality lab results,
  energy/backup, discharge, structure/loading, food-safety, and climate-control
  evidence requirements.
- [x] Silvopasture/agroforestry: land-use authority, soils, slope, hydrology,
  water budget, existing canopy/fuels, livestock/wildlife, access, and local
  species/regulatory evidence requirements.
- [x] Emit facts/features/gaps only. Recommendation generation requires a
  separately authorized reviewed policy/effect-evidence workstream.

## Phase 5 — validation and expansion

- [x] Run the repository’s full Python and root TypeScript lint/type/test
  contracts once, plus disposable PostgreSQL migration execution.
- [x] Run a read-only audit against the persistent warehouse after the
  disposable proof; confirm it remains at `0007` and historical/forecast
  counts and checksums did not change.
- [x] Obtain independent review and fix all substantive findings before close.
- [x] Expand by adapters: US national baselines, Canada federal/provincial,
  Mexico INEGI/federal/state/municipal, and local parcel/building layers.
  *Re-scoped 2026-08-14: the US national-baseline families are already served
  by existing ingest lanes (MTBS, SoilGrids soil, USDM, USGS NWIS, NDVI/
  vegetation, watersheds). Remaining jurisdictional expansion (Canada, Mexico,
  parcel/building layers) is encoded per-source in the machine-readable
  manifest registry (`ingest/validation/source_manifests.py`) with
  `planned`/`blocked` states and licence gates, and proceeds
  adapter-by-adapter under this plan's own per-source gate (see "Current
  state" note above) rather than as a single track item.*

## Storage, refresh, compute, and hosting estimate

Assumptions: fewer than 100 concurrent users, one initial city, raw immutable
objects in R2, normalized/vector/hot facts in PostGIS, COG/Parquet windowing
instead of copying continent-wide rasters, and no always-on ML/GPU worker.

| Stage | Stored data | Refresh/compute | Expected monthly run cost |
|---|---|---|---|
| Pilot proof | <10 MB raw/receipts, <50 MB PostGIS | Seconds to minutes on local CPU; manual only | $0 incremental local |
| One city, lean | 25–100 GB raw/cached windows; 5–20 GB hot DB | Daily active/weather deltas, weekly drought/fire, annual land/soil/cadastre; 1–4 CPU-hours/day | About $40–75 |
| Several cities | 0.25–1 TB raw/history; 25–100 GB hot DB | 4–16 CPU-hours/day with queued batch workers | About $80–200 |

The one-city planning envelope is deliberately source-shaped:

| Source family | Windowed raw/history allowance | Typical refresh |
|---|---:|---|
| Boundaries, roads, hydrography, WUI, conservation, parcels where licensed | 0.5-5 GB | Monthly to annual; incident vectors daily |
| 10-30 m elevation, land cover, fuels, canopy, soils | 10-40 GB | Annual/versioned |
| Weather, drought, soil-moisture, fire and lightning time windows | 5-25 GB | Hourly to weekly with retention caps |
| Buildings/imagery where explicitly open and justified | 5-20 GB | Quarterly to annual |
| Checksums, manifests, normalized features, indexes and backup headroom | 4.5-10 GB | Per release |

Exact adapter estimates must be computed from provider byte counts and AOI tile
indexes before enabling a source; the upper bound is capacity planning, not a
promise to copy every available layer.

Current official list prices used for the estimate:

- [Railway Pro](https://docs.railway.com/pricing/plans): $20/month minimum
  usage commitment; RAM $10/GB-month, CPU $20/vCPU-month, volume
  $0.15/GB-month, egress $0.05/GB.
- [Cloudflare R2](https://developers.cloudflare.com/r2/pricing/): standard
  storage $0.015/GB-month, 10 GB/month free; 1M Class A and 10M Class B
  operations/month free; direct egress is free.

A lean continuous footprint (roughly 2.5 GB RAM, 0.45 vCPU, 30 GB Railway
volume, 25 GB egress, and 100 GB R2) is about **$41/month** at list price.
Allow **$40–75/month** for logs, burst compute, backups, and normal variance.
One-meter DEM or imagery must remain provider-hosted/windowed where possible:
copying a whole metro can add hundreds of GB and is not justified in phase one.
