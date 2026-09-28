# `warehouse/schemas` — one Parquet schema module per layer slug

## Responsibility
The per-lane half of the schema registry. Each physical lane owns exactly one autoload module here,
named for its slug with hyphens replaced by underscores:
`fire-detections` to `fire_detections.py`. Stream **S0** created this package; it registers
nothing in it.

## The contract a lane module must satisfy
```python
from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

SENSORS_SCHEMA = register_stream_schema(
    ParquetStreamSchema(
        name="sensors",                      # the layer slug, verbatim from geo.layers.name
        arrow_schema=pa.schema([...]),
        sort_columns=("...",),               # the grain, sorted before every write
    )
)
```
- **Register at import time.** `get_stream_schema("sensors")` autoloads this module and expects
  the side effect; a module that defines a schema without registering it fails loudly.
- **`name` must equal the layer slug.** It is simultaneously the registry key, this module's name,
  and the `layer=<slug>/` object prefix the lane is allowed to write under.
- **Observed and forecast share one schema.** Per `layer-lanes.md` §2 a forecast row and an
  observed row differ in `kind` and provenance, never in shape. Forecast provenance columns
  (§3: `forecast_run_id`, `random_seed`, `ensemble_size`, `horizon_days`, `issued_on`, and
  `quantile` or `draw_index`) belong in the same schema, nullable on the observed side.
- **No cross-lane imports.** A shared column set moves down into `foundation`, in its own commit.

## Two lanes here are READ-ONLY: another service writes them

`fire_risk.py` and `weather_forecast.py` register schemas for streams this service never produces.
`services/plantgeo-ml-service` writes both (owner decision D2, 2026-09-18, and FR-12 of track
`plantgeo_ml_service_20260918`); agri-data-service reads them for serving and registers them here so
the lane registry, the Parquet readers and the slider catalogue can resolve the slugs.

**`weather_forecast.py` is not a reversal of the owner's 2026-09-19 deletion.** That decision was
about ADMISSION -- agri admits no provider projection, and this service still ingests none: the
deleted ingest packages stay deleted and nothing here fetches Open-Meteo forecast hours. The ML
service writes this stream and agri only reads it for serving. See
`conductor/tracks/plantgeo_ml_service_20260918/spec.md` FR-12.

Both modules are EXACT copies of the ML service's own pinned schemas, held apart rather than
imported because the two services deploy independently. A change on either side is a change on both.
`fire-risk` is also the one registered stream whose schema already carries the six forecast
provenance columns, because it has no observed side at all -- see `FORECAST_ORIGINATED_STREAMS` in
`warehouse/parquet/schema.py`, which is what keeps `get_stream_schema(name, "forecast")` from
appending those names a second time.

### `fire-risk` carries a DETERMINISTIC provenance set, and it is the only lane that does

`conductor/code_styleguides/layer-lanes.md:208-213` (section 3, amended 2026-09-19) says a product
emitting one calibrated value per cell-day writes the literal `quantile = "point"` with
`ensemble_size = 1`, a recorded `random_seed` and a `model_artifact_sha256`. `fire_risk.py` therefore
declares `DETERMINISTIC_PROVENANCE_FIELDS` locally, derived from the shared tuple by substituting
that one field, and the shared `FORECAST_PROVENANCE_FIELDS` keeps `quantile` a `float64`
(`warehouse/parquet/schema.py:97`) for the drawn lanes that really do report 0.1/0.5/0.9. Two
streams, two provenance sets, no union type and no nullable second column.

**The deterministic set is NOT shared.** It lives in the lane module because `fire-risk` is the only
slug in `FORECAST_ORIGINATED_STREAMS` (`warehouse/parquet/schema.py:120`) and the shared derivation
`forecast_stream_schema()` appends the numeric set unconditionally
(`warehouse/parquet/schema.py:123-136`) -- a shared `DETERMINISTIC_*` name would advertise a mode
that function cannot be asked for. If a second deterministic lane ever lands, lift the tuple then.

**Nothing else moved, and that is checkable.** The sort grain is still
`FIRE_RISK_GRAIN + FORECAST_PROVENANCE_GRAIN` and the tier key still names the same provenance
columns, because both are lists of NAMES (`warehouse/parquet/schema.py:105`) and the ML copy builds
its sort key from the same three
(`services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/streams.py:281-286`);
`validate_derivation_against_schema` constrains membership and nullability, never type
(`warehouse/parquet/tiers.py:862-887`). The registry check that pins the provenance block to the end
of this schema compares names too (`tests/parquet/test_stream_schema_registry.py:274`), so it holds
across the type change. Byte-for-byte equality with the writer is enforced by
`tests/parquet/test_ml_schema_parity.py`, which reads the sibling's module off disk rather than
trusting either module header.

## `calendar.py` is a dimension, not a layer
The conformed date dimension is registered here like any other stream, but it is not a
`geo.layers` slug and has no source system: every column is a function of `calendar_day` alone, and
the generator is `foundation/parquet/calendar.py` (stdlib only). It is `static_lookup` and
`horizon: none`, so it never writes `kind=forecast`.

**Lanes key to it by value; no lane schema gains a foreign-key column for it.** A lane's own
role-named date column — `observed_day`, `release_day`, `snapshot_day`, `valid_date`, `issued_on` —
joins to `calendar_day`. Collapsing those roles into one key is exactly what
`docs/holonic-kimball-modeling.md` forbids.

## `signal` is already registered elsewhere
The signal plane's twelve-column schema is frozen owner-decided truth and lives in
`warehouse/parquet/schema.py` (`SIGNAL_PLANE_SCHEMA`). If S3 adds `signal.py` here it must
re-export that object, never restate the columns — one canonical definition per concept.

## Snapshot-derived signal products

`warehouse/parquet/snapshot_signal_product.py` is the only family helper. It lives below this
lane-module directory so no lane imports a sibling lane. VPD, dew point, three air-temperature
products, and wind speed clone the frozen twelve-column signal schema and tier derivation with only
the physical stream name changed. Their separate modules preserve separate `layer=<slug>/` prefixes.

Relative humidity, shortwave radiation, precipitation, and the three ERA5-Land soil-moisture
depths share the exact 33-field snapshot-lineage contract emitted by their completed builders:
twelve serving fields plus twenty-one source-lineage fields. Earlier task prose called this a
32-column shape, but the four source modules and completed artifacts all contain 33; integration
must not drop a lineage field to fit the stale count. Coarse rungs keep snapshot identity and null
row-level locators that cannot honestly name one contributor.

The lineage columns are scoped by `source_snapshot_id`: on a `direct:<sha256>` row they name the
NASA POWER response object the value was read from, not a row of `agri.signal_observation`.

The four soil-temperature depths share the completed bundle's separate 21-field lane contract.
Their coarse cells sum physical-candidate counts, null selected-row identity, and compute
`lineage_sha256` from sorted child digests with one newline per value. The helper registers these
storage and zoom contracts only; it does not rerun or rewrite the immutable snapshot builders.

## `soil_survey.py`: SSURGO delineations, written only by the offline candidate CLI

The rationale that used to sit in comments inside `soil_survey.py` lives here since the 2026-09-27
native-geometry port (slice S2).

**Grain.** One row is one SSURGO delineation, keyed on `mupolygonkey`
(`docs/lanes/soil-survey.md` section 4). It is explicitly not `mukey`: one Boise viewport measured
683 delineations collapsing onto 98 distinct `mukey` values. `natural_key` is the namespaced
identity `usda-sda:<mupolygonkey>`; `mukey` is informational only and is never a join or sort key.

**Columns.** The first fifteen columns mirror what the retired Postgres ingest persisted into
`geo.geometry` and `geo.features`, rendered from the geometry dimension rather than the properties
JSONB. Points worth keeping:
- `hydric_rating` is tri-state. SSURGO rates a component Yes or No, or leaves it unranked; unranked
  stays null and is never coerced to false.
- `survey_area_vintage` is `sacatalog.saverest` at UTC midnight. The upstream value carries no
  timezone, so no clock time is fabricated (`docs/lanes/soil-survey.md` section 5, point 5).
- `release_day` is the day the release represents (the newest vintage in a shard), a constant on
  every row. It is not the same fact as any one delineation's own vintage, the same distinction
  `watersheds.py` draws for HUC12 boundaries.
- `geometry_id` is **nullable** since the port. A source-direct row has no Postgres geometry UUID,
  so the offline CLI writes null rather than inventing one.
- `geometry_wkb` is WKB, not GeoJSON, and carries no SRID; readers assume EPSG:4326 out of band like
  every other geometry column in this warehouse.
- `producer` is the constant `usda-sda`, kept as a real column so a second SSURGO producer
  namespace can never blend silently into this one.

**Columns added by the port** (all nullable, so pre-port fixtures and readers keep their shape):
- `bbox_west`, `bbox_south`, `bbox_east`, `bbox_north`: each row's own coordinate extent, written by
  `pipeline/direct/soil_survey/prepare.py` and proved against the WKB by
  `sql/pipeline/ssurgo_part_bounds.sql`. Serving selects rows by these columns, never by a GEOS
  predicate, because a GEOS predicate rejects the invalid rings owner Q4 says must still be served.
- `geometry_quality`: `valid`, `repaired` (`ST_MakeValid` stayed polygonal and the repair is what
  is served) or `invalid_unrepaired` (the original bytes are served with this label). Null appears
  only on rows written before the port; `prepare.py` always sets it and
  `pipeline/validation/soil_survey.py::validate_soil_survey_candidate` refuses a candidate part
  whose labels disagree with its manifest.

**Tiers.** The registered derivation simplifies only: no dissolve, no aggregation, and **no area
floor**. `min_area_tier_squares=1.0` was tried first and it empties this lane at z0: one z0 grid
square is 25 square degrees while the whole PNW universe is about 10 x 10 degrees, so every feature
drops and z0-z4 would be a blank map. The derivation stays registered for the tier machinery, but
the offline candidate carries rung 13 only (owner Q1); low zoom is a separate artifact (Go-5).

## `availability_index.py` is publication state, not a lane data schema

The availability index is one canonical standalone Arrow schema shared by every time-bearing lane;
it is deliberately not a `ParquetStreamSchema` registry entry. Rows are terminal `(day, rung)`
claims, not renderable observations. `required_rungs` uses the foundation's canonical ordered
identity `(0, 5, 9, 13)`, and `data_receipts` is an Arrow-native ordered list of key/SHA structs so
multi-part rungs retain every immutable part receipt without embedding a second JSON language in
Parquet.
