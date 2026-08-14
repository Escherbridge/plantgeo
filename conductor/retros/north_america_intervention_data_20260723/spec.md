---
type: specification
---

# North American intervention evidence ingestion

## Goal

Create a free/open, resolution-aware evidence plane that can support defensible
city/landscape and parcel/property planning inputs for wildfire prevention,
drought-system reversal, watershed restoration, aquaponics, hydroponics,
silvopasture, and agroforestry without presenting coarse context as site truth.

The first pilot is Boise, Idaho and the named Hillside to Hollow Reserve
property represented by OpenStreetMap way `674700373`. The ODbL boundary is
open but non-cadastral, so it supports a stable property identity and
neighborhood context only. Legal parcel, ownership, and survey-grade boundary
evidence remain known gaps.

## Invariants

- Existing NASA POWER, ERA5-Land, USDM, warehouse, forecast, and Conductor
  history remain unchanged and queryable.
- Railway, schedules, deployment state, publication pointers, forecasts,
  `strategy_selection`, strategies, and recommendations are out of scope.
- Provider bytes are captured locally before transformation, checksummed, and
  accompanied by source URL, query, retrieval time, provider version, HTTP
  validators, licence snapshot, byte count, and transform version.
- Provider facts, model-derived features, known gaps, and later recommendations
  are different record types. This track creates no recommendation rows.
- Every geometry and evidence input declares native support/resolution, maximum
  inference scale, confidence basis, and a method/metric name that distinguishes
  direct provider facts, provider classifications, derived overlays, and gaps.
- Resampling, interpolation, clipping, or joining never upgrades native support.
- No evidence produced here is a validated life-safety prediction or a legal,
  cadastral, wetland, water-right, building-code, or engineering determination.
- `conductor/` remains an OKF bundle; every new Markdown artifact has `type`
  frontmatter.

## Pilot acceptance criteria

1. A reviewed capture plan retrieves the 2025 Census Boise incorporated-place
   boundary, OSM Hillside to Hollow Reserve way `674700373` version 19, and the
   complete USDA Forest Service 2020 WUI census-block response returned by a
   pinned bounding-box AOI query into a local immutable cache. The OSM version
   claim is separately backed by a checksummed, version-specific official OSM
   API response rather than inferred from mutable Nominatim output.
2. Checksums, byte sizes, provider version/availability, query parameters,
   attribution, licence status, and source-scale limits are validated before a
   warehouse transaction begins.
3. An additive Alembic migration defines immutable normalized source features,
   stable city/property subjects, source/evidence lineage, and separate
   observed/model-derived/gap inputs.
4. PostGIS validates/normalizes SRID 4326 geometry, retains the raw geometry
   checksum, and computes geodesic area and WUI overlap using a versioned SQL
   method.
5. The pilot proves a city subject and a named-property subject. It records
   USFS 2020 WUI only as census-block/neighborhood exposure context. Existing
   USDM history is preserved but excluded from this open-only release set until
   authoritative redistribution terms are archived.
6. The parcel output contains practical information requirements for wildfire,
   watershed restoration, aquaponics/hydroponics, silvopasture, and agroforestry
   but makes no intervention recommendation before local inspections,
   soil/water tests, ownership/use authority, and regulatory review.
7. One integrated test/lint/migration validation sweep passes after all changes,
   followed by an independent design/code review.

## Source programme

The authoritative coverage/resolution/licence register is
[`docs/north-america-intervention-source-matrix.md`](../../../docs/north-america-intervention-source-matrix.md).
The ingestion order is:

1. National/continental public baselines with clear rights and stable bulk
   access.
2. Jurisdictional parcels, structures, WUI, infrastructure, conservation, and
   regulatory layers only through a coverage/licence allowlist.
3. Versioned historical/operational environmental signals with explicit
   latency and spatial-support gates.
4. Field observations, surveys, lab tests, and professional determinations as
   future first-class evidence, never overwritten by remote data.

## Scale contract

| Maximum scale | Required evidence |
|---|---|
| Regional | Kilometer-scale drought, soil moisture, climate, and broad model products |
| City/landscape | 10–250 m land, fuels, terrain, hydrography, exposure, and observed station context |
| Neighborhood | Locally current vectors or roughly 1–30 m rasters with documented completeness |
| Parcel/property | Approved parcel/property geometry plus genuinely compatible source support; contextual inputs stay labeled context |
| Structure | Verified local/authoritative footprint and structure attributes or reviewed 1–2 m evidence |

## Known blockers

- No complete authoritative openly redistributable North American parcel fabric
  exists. Ada/Boise redistribution permission is unresolved, and the pilot's
  OSM property boundary is explicitly non-cadastral.
- Boise's municipal WUI layer is excluded because open redistribution terms
  were not verified. The open USFS WUI replacement is 2020 census-block
  exposure context, not current parcel fuels or a regulatory determination.
- Public pipe, pressure, capacity, hydrant, building-condition, and water-right
  coverage is incomplete and sometimes security-sensitive.
- USDM has no explicit redistribution licence in the reviewed provider pages;
  it remains landscape context in the existing local warehouse.
- Mexico SATIF is CC BY-NC and is excluded from a commercial path without
  permission. Mexico drought polygons are request-gated.
