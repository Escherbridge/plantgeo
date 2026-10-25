# `pipeline/lanes/watersheds/` — the watersheds layer package

Moved verbatim from `pipeline/direct/watersheds/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Watersheds".
The `s-watersheds` swarm slice builds the config-lane strategy beside these modules.

| module | what |
|---|---|
| `source.py` | the NHDPlus HR WBDHU12 fetch and its `WatershedRecord` identity |
| `watermark.py` | the `max(loaddate)` source watermark `lane_registry.py` imports |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`lane_registry.py` imports `watermark.py`, so neither module may import `LANE_REGISTRY`.
