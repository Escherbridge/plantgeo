# `pipeline/lanes/land_context/` — the land-context layer package

Moved verbatim from `pipeline/direct/land_context/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/land_context/AGENTS.md`.
The `s-land-context` swarm slice builds the config-lane strategy beside these modules.

| module | what |
|---|---|
| `products.py` | the BLM ArcGIS products, archive root and history floor |
| `source.py` | the bounded BLM capture and immutable replay |
| `source_protocol.py` | `LandContextSource` and `BLM_LAND_CONTEXT_SOURCE` for `pipeline/source_bindings.py` |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only (plus `pipeline/errors.py`, a residual the strategy should drop).
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`pipeline/direct/land_context/watermark.py` did NOT move: it was not in the task's move list, and
`lane_registry.py` still imports it from `pipeline/direct/` (a Phase-7 residual).
