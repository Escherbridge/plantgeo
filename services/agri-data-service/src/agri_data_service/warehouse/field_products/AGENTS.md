# `warehouse/field_products/` — the field-product catalogues

| module | what | legacy shim |
|---|---|---|
| `climate.py` | the eleven NASA POWER products, source parameters, floors and publication lags | `pipeline/direct/climate/products.py` |
| `soil.py` | the eight ERA5-Land (Open-Meteo archive) soil products, floors and lags | `pipeline/direct/soil/products.py` |
| `vegetation.py` | the one Sentinel-2 NDVI product and its writer start day | `pipeline/direct/vegetation/products.py` |

Moved verbatim by `p4-extract` (track `config_driven_ingestion_20260926`, Phase 4) because
`pipeline/parquet/lane_registry.py`, `parquet_ops/coverage.py`, `parquet_ops/snapshot_product_catalog.py`,
`agent/window_distribution.py` and the config-lane strategies all read them, and Phase 7 deletes
`pipeline/direct/**`. The legacy files are re-export shims (`DEPRECATED_ALIASES.md`, deleted at 7D-n
of the supersession cleanup lane). The rationale for each floor and lag stays in each module's
comments and in `pipeline/direct/AGENTS.md`.

Layer L1: these modules import `warehouse` only (`warehouse/parquet/schema.py`, `warehouse/schemas/`),
never `pipeline` (`tests/test_layer_import_contract.py`). That is what lets the registry, the readers
and the strategies share one catalogue without an import cycle.
