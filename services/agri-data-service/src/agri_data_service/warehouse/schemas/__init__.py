"""Per-lane Parquet schemas, one module per layer slug (L1).

`agri_data_service.warehouse.parquet.schema.get_stream_schema` autoloads from here: slug
`fire-detections` resolves to `fire_detections.py`, which must call `register_stream_schema`
at import time. See `AGENTS.md` in this directory.
"""
