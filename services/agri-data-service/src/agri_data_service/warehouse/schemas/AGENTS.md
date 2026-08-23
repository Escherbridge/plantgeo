# `warehouse/schemas` — one Parquet schema module per layer slug

## Responsibility
The per-lane half of the schema registry. Each of the eleven lanes (`layer-lanes.md` §1) owns
exactly one module here, named for its slug with hyphens replaced by underscores:
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
The signal plane's ten-column schema is frozen owner-decided truth and lives in
`warehouse/parquet/schema.py` (`SIGNAL_PLANE_SCHEMA`). If S3 adds `signal.py` here it must
re-export that object, never restate the columns — one canonical definition per concept.
