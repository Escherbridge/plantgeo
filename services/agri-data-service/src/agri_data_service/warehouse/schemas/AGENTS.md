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

## `availability_index.py` is publication state, not a lane data schema

The availability index is one canonical standalone Arrow schema shared by every time-bearing lane;
it is deliberately not a `ParquetStreamSchema` registry entry. Rows are terminal `(day, rung)`
claims, not renderable observations. `required_rungs` uses the foundation's canonical ordered
identity `(0, 5, 9, 13)`, and `data_receipts` is an Arrow-native ordered list of key/SHA structs so
multi-part rungs retain every immutable part receipt without embedding a second JSON language in
Parquet.
