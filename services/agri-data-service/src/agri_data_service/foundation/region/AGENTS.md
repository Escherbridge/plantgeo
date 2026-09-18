# `foundation/region` — the manifest, not a fork's ancestor

`federation.md` section 1 rules: one typed region manifest, one place for every footprint literal.
This package is that place for the service tree. `manifest.py` defines the frozen Pydantic model;
`pnw.json` is the pilot's only data file, and is the single source of truth the web tree's parity
test (`src/__tests__/region/manifest-parity.test.ts`) also reads — see `src/lib/region/AGENTS.md`
for the other half of that contract.

## Why the data lives in JSON, not a Python literal

A `Region` object built directly in `manifest.py` would be one more place the numbers are typed
in, alongside the TypeScript `pnw.ts` object the styleguide also asks for. Loading both trees from
the same `pnw.json` means a changed coordinate is one edit instead of two kept manually in sync,
and the parity test can diff a JSON file against a TypeScript object without parsing Python.

## `default_camera_envelope`

Added when `coverage-region.ts` was first pointed at this manifest: that migration must be
behaviour-neutral, so `FALLBACK_COVERAGE_BBOX` (the client's opening-camera/`ServiceAreaLayer`
fallback) could not simply read `envelope` — `envelope` is the wider named-region box
`(-126, 41, -110, 50)`, while `FALLBACK_COVERAGE_BBOX` had always carried the narrower MTBS burn
box `(-125, 42, -111, 49)`. `default_camera_envelope` gives the manifest a field for that exact
pre-existing value, so the fallback keeps its old behaviour while still reading from the manifest
instead of a private literal. It happens to equal `sub_envelopes.burn_severity` today — same
coincidence `sub_envelopes.burn_severity`'s own note below describes, not a rule that the two must
match going forward.

## Why `envelope` and `sub_envelopes` disagree, on purpose, today

Migration step 1 (`federation.md` §5) lands the manifest and points `coverage-region.ts` at it.
Steps 2–4 move the *other* footprint literals in. Three numbers already existed for "the PNW box"
before this package did, and they are genuinely different claims:

- `envelope` = `(-126, 41, -110, 50)` — the **named region** row from
  `src/lib/map/coverage-region.ts`'s `NAMED_COVERAGE_REGIONS`, the widest of the three and the one
  this manifest's top-level field carries.
- `sub_envelopes.burn_severity` = `(-125, 42, -111, 49)` — `PACIFIC_NORTHWEST_BBOX`
  (`ingest/mtbs.py:76`), restated in three `pipeline/direct/burn_severity/*.py` files and in the
  `parquet-trpc-readers/burn-severity.ts` reader's `SUPPORTED_BURN_SNAPSHOT_SCOPE` default. It is
  also the current `FALLBACK_COVERAGE_BBOX` value in `coverage-region.ts` — a coincidence of which
  literal someone reached for, not a rule that the fallback camera and the burn envelope must
  match.
- `sub_envelopes.botanical_seed` = `(-125, 41, -110, 50)` — `SEED_ENVELOPE`
  (`foundation/botanical_occurrences/coordinates.py:22`).

None of the three is wrong; they describe three different things (the platform's named footprint,
MTBS's admitted bbox, and the botanical-occurrence classifier's admitted-coverage envelope) that
happened to be typed as three separate near-identical tuples. `sub_envelopes` gives step 2 a place
to point `PACIFIC_NORTHWEST_BBOX` and `SEED_ENVELOPE` at instead of restating them a fourth time;
it is not itself in `federation.md` §1's required-fields list, and a later step may decide one or
both should collapse into `envelope` once every caller of the narrower box has been reviewed.

## Lattice pitch and origin rule, cited

`lattice_pitch_degrees` (`0.01`) and `lattice_origin_rule` (`floor_to_cell_origin`) are read from
`warehouse/parquet/tiers.py`, not invented here:

- `TIER_RESOLUTION_DEGREES[9] = 0.01` is the finest **derived** rung's resolution — the base z13
  rung is deliberately unpitched ("the base rung, written by the lane's own exporter at whatever
  grain its source has", `tiers.py`'s own module docstring), so `0.01` is the finest fixed pitch the
  lattice itself declares, not a claim about every lane's raw grain.
- `floor_to_cell_origin` names `floor_to_resolution`'s algorithm (`tiers.py`): a coordinate
  maps onto its cell's origin via `floor(v / r) * r`, with a value within `FLOOR_SNAP_TOLERANCE`
  of an exact multiple of `r` snapped to that multiple first, so a coordinate that IS a lattice
  edge lands in the same cell on every host and every frame length.

## Layer bindings, cited

`enabled_layers` lists thirteen bindings. `layer-lanes.md` §1 itself enumerates "thirteen registered
streams" as the eleven original `geo.layers` slugs plus `signal` and `drought` plus `calendar`
(§1a) — **this manifest's thirteen are not that same set**: `calendar` is a control-plane
dimension with no source binding of its own (every lane reads it, it reads nothing), so it stays out
of `enabled_layers` the same way `interventions` (below) stays out; `botanical-occurrences` is a
served plane (`planes/botanical_occurrences.py`, `foundation/botanical_occurrences/`) that
`layer-lanes.md` §1 never enumerates at all, added here because it is a real layer a region binds a
source for, per `federation.md` §1's "enabled layers with their source binding". The count agreeing
with "thirteen" is what STYLE-REVIEW-W1.md B1 asked to fix — the earlier text claimed the eleven
original slugs matched that citation, which `layer-lanes.md` never said; this text does not repeat
that mistake by implying the two thirteens are the same list.

Each binding's source is grepped from the `ingest/` or `pipeline/direct/` module that actually pulls
it, not assumed: `ingest/mtbs.py` (burn-severity → MTBS), `ingest/firms.py` (fire-detections →
FIRMS), `ingest/vegetation.py` (vegetation → Sentinel-2 L2A NDVI over STAC, **not MODIS** — the
module's own docstring says so), `ingest/wfigs.py` (fire-perimeters → WFIGS), `ingest/usgs_nwis.py`
(water-gauges → USGS NWIS), `ingest/sensors.py` (sensors → NOAA NWS ground stations, **not GBIF**),
`ingest/watersheds.py` (watersheds → HydroSHEDS), `ingest/open_meteo.py` and
`pipeline/direct/weather_observations/` (weather-observations → Open-Meteo), and
`ingest/evacuation_zones.py` (evacuation-zones → the Oregon OEM ArcGIS FeatureServer — a single
state's own portal, so `regional` despite not being one of the styleguide's example sources).
`soil-survey` binds `ssurgo`: the ingest module was retired in the 2026-09 Postgres cleanup, but
`pipeline/validation/soil_survey.py` and `planes/soil_survey.py` still name the source.
`drought` binds `usdm` (`pipeline/direct/drought/adapter.py` reads `ingest/usdm.py`'s
`usdm_source_url`, `planes/drought.py`'s own docstring: "a weekly USDM cadence"). `signal` binds
`era5_land_and_nasa_power`: `pipeline/parquet/lane_registry.py`'s `SIGNAL_PLANE_STREAM` registration
names both producers by lag ("Lag 9 is ERA5-Land's measured PUBLICATION_LAG_DAYS ...; NASA POWER's
is 5") and neither alone accounts for the plane, so the binding names the blend rather than picking
one and hiding the other. `botanical-occurrences` binds `gbif`:
`pipeline/direct/botanical_occurrences/fetch.py` pulls from `api.gbif.org` (GBIF's Download API) as
well as the PNW Herbaria and Canadensys IPT endpoints GBIF itself aggregates, and GBIF is the
publisher-facing name the occurrence identity and rights review already use.

`interventions` binds NO source here: it has no Parquet lane at all and stays in Postgres per
RUNBOOK §0.26.1, so it is a governed absence (`federation.md` §2 — "the platform must run with a
layer unbound") rather than a bound layer with a placeholder source. A prior version of this
manifest gave it `postgres_interventions` with `coverage: "regional"`; removed rather than kept,
because `SourceCoverage` has only `global`/`regional` and neither describes "not a Parquet lane at
all" — a later region without Postgres interventions should read this layer's absence from
`enabled_layers` as "not available here", not copy a placeholder that was never a real source
(STYLE-REVIEW-W1.md B1). If a future manifest needs to distinguish a governed-absence layer from an
unbound-but-wanted one, that is a `SourceCoverage` schema change made deliberately, in its own
commit, not a value invented to fit the existing enum.

Coverage: MTBS, SSURGO, WFIGS, USGS NWIS, NOAA NWS, USDM and the Oregon OEM feed are US-specific
(`regional`); FIRMS, Sentinel-2, Open-Meteo, HydroSHEDS, the ERA5-Land/NASA POWER blend and GBIF
serve outside the US too (`global`).

## Timezone

`America/Los_Angeles` is the manifest's one declared zone. The pilot's own `admin_codes`
(`US-WA`, `US-OR`, `US-ID`) span two IANA zones — Idaho's panhandle is Pacific, the rest of Idaho
is Mountain — and this field picks the dominant one rather than modelling a per-admin-code zone
table; a region whose footprint straddles zones more evenly will need that modelling; this pilot
does not motivate it yet.
