---
type: track-evidence
track: gapless_parquet_publication_20260901
slice: p2-era5-soil-direct-forward
status: authored_unverified
observed_at: 2026-09-02
---

# The ERA5-Land soil forward writer, and the NASA POWER soil-wetness depths

## Verdict

Two forward writers now cover every stream the 2026-09-01 assessment measured a tail on. Both ship
in SHADOW. NOTHING WAS RUN: no test, no lint, no type check, no network request, no object-store
write and no database connection. Everything below is authored evidence, to be swept and reviewed by
a separate lane.

## What exists

**`src/agri_data_service/pipeline/direct/soil/`** publishes the eight ERA5-Land streams the browser
draws under three soil toggles, mirroring `pipeline/direct/climate/` module for module
(`products.py`, `support.py`, `source.py`, `rows.py`, `adapter.py`, `forward.py`, `__main__.py`).
Entry point `python -m agri_data_service.pipeline.direct.soil --product {moisture|temperature|vpd|all}`.

| toggle | streams | source variable | signal | unit | row shape |
|---|---|---|---|---|---|
| moisture | `soil-field-moisture-{0-7cm,7-28cm,28-100cm}` | `soil_moisture_*_mean` | `soil_water_content_layer_{1,2,3}` | m^3/m^3 | 33-column snapshot lineage |
| temperature | `soil-temperature-{0-to-7cm,7-to-28cm,28-to-100cm,100-to-255cm}` | `soil_temperature_*_mean` | `soil_temperature_level_{1,2,3,4}` | C | 21-column lane |
| vpd | `soil-field-vpd` | `vapour_pressure_deficit_max` | `vapor_pressure_deficit` | kPa | 12-column signal plane |

**`pipeline/direct/climate/`** gained the three NASA POWER soil-wetness depths
(`soil-wetness-{surface,root-zone,profile}`, parameters `GWETTOP`/`GWETROOT`/`GWETPROF`, signal
`soil_wetness_*`, unit `fraction_of_saturation`, a third 19-column row shape). They belong there and
not in the soil package: they are POWER parameters on the POWER lattice at the POWER lag, and one
point request already returns every parameter the URL asked for.

**Registry and scheduler.** `LANE_REGISTRATIONS` goes 21 -> 32 and `LANE_SPECS` 47 -> 59 (32 generic
`parquet-*` specs, 11 PostgreSQL ingestion lanes, 9 jobs lanes, 7 migration inputs). The refusing
adapter became a factory (`_source_direct_refusal`) so each of the nineteen source-direct lanes names
the writer that actually owns it. `_SOURCE_DIRECT_SLUGS` is now derived from `_DIRECT_WRITER_BY_SLUG`,
which maps each slug to ITS writer, so a generic spec conflicts with the lane that would really
contend for its lane-day lock. New executor spec `soil-era5-land-direct-forward`, hourly at :50,
`legacy_owners=()`, timeout 1200 s, `writer_floor` 2026-08-03, conflicts declared both ways with its
eight generic specs and disjoint from the climate writer's eleven.

**Readers.** All fourteen snapshot products now carry a `forward_first_day`: nine at
`CLIMATE_DIRECT_WRITER_START_DAY` (2026-08-07, six climate products plus the three soil-wetness
lanes) and five at `SOIL_DIRECT_WRITER_START_DAY` (2026-08-03, VPD plus the four temperature bands).
The three `soil-field-moisture-*` streams needed no descriptor change: they are dedicated slider
prefixes in the ordinary lane layout, not snapshot products.

## The one decision that departs from the brief

The brief specified a writer over `execution/historical_era5.py`, the Copernicus CDS lane, and named
the missing `CDSAPI_*` credentials as the blocker to activation. That writer would have been wrong
on all three products, and the artifacts that wrote the history say so:

1. Every historical row of all eight streams carries `data_source_key` `open-meteo-era5-land-archive`
   and `support_key` `era5-land-0.1deg`. The CDS lane never persisted a warehouse row; its own module
   docstring records that `agri.data_source` has no `era5-land` key at all.
2. The CDS plan requests a 1.0-degree OUTPUT grid; the history sits on the 1,568-cell 0.25-degree
   `sentinel2-ndvi-0p25deg` lattice at ERA5-Land's native 0.1 degrees. A day on the coarser grid is
   not comparable with the days it would claim to extend.
3. The CDS lane carries one of the three moisture depths and has no VPD variable at all. VPD is not
   derived by the historical path either: Open-Meteo publishes `vapour_pressure_deficit_max` as a
   daily variable of the same `era5_land` model, and that published series IS the history.

The writer therefore reads the Open-Meteo ERA5-Land archive -- the same source, support, lattice and
units the immutable days were written from. A consequence worth stating plainly: **this lane is not
credential-blocked.** The archive host is keyless.

## What blocks activation

Nothing structural, and no credential. What is owed before an operator names either lane in the
executor's allow-list:

1. **A sweep.** Nothing here has been run. Predicted failure surface and the exact files are in the
   slice report; the join owns the single test/lint/type pass.
2. **An adversarial review** of the two writers and the registry/scheduler changes, in a context that
   did not author them.
3. **A reader that can see forward days.** Fourteen snapshot products now route days at or after
   their edge through the live lane path. With no forward days written the observable behaviour is
   unchanged, but under `PARQUET_COVERAGE_AUTHORITY=availability` a product whose lane has no
   availability index has its forward half WITHHELD -- which is the same trade-off the six climate
   products already accepted, now extended to eight more.
4. **One live proving run per writer**, off the scheduler, at `--max-days 1`. Neither lane has ever
   issued a request against its live service. Two specific things the first run decides:
   - whether the soil-temperature bands really share the 1,470-cell land-sea mask measured for
     moisture and VPD. They are refused, not published thin, if they do not.
   - whether an eleven-parameter POWER point response is shaped like the eight-parameter capture. No
     real eleven-parameter body exists in the tree; the fixture covering that shape is synthetic and
     labelled as such.
5. **Environment, variable NAMES only, no values:** the ordinary object-store settings and
   `LOCAL_SOURCE_LOADER_DATABASE_URL` (or its existing fallback), pointing at a database whose
   `agri.spatial_cell` holds the `sentinel2-ndvi-0p25deg` lattice at 1,568 cells and the
   `nasa-power-0.5-degree` lattice at 397. `OPEN_METEO_API_KEY` is OPTIONAL and lifts a quota wall
   only; `CDSAPI_URL`/`CDSAPI_KEY` are NOT read by either writer and are no longer a precondition for
   any of these eleven soil streams.

## Deliberately not done

- `soil-wetness-*` and `soil-temperature-*` were not merged into one family. They look alike and are
  different upstreams with different lattices, lags and column sets.
- `parse_open_meteo_archive_payload` was not reused: it is reachable only through a plan whose window
  must be exactly four calendar years, and a forward writer asks for one day. Every governed guard
  underneath it IS reused; only three private checks are restated, each beside a comment naming its
  sibling.
- The three new `soil-wetness-*` streams are not in `tests/parquet/test_snapshot_signal_product_schemas.py`,
  which parametrizes over explicit tuples per family. A 19-field family belongs there; that file was
  outside this slice's write set.
