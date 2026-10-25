# `pipeline/lanes/drought/` — the drought layer package

Moved verbatim from `pipeline/direct/drought/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Drought".
The `s-drought` swarm slice builds the config-lane strategy beside these modules.

| module | what |
|---|---|
| `source_protocol.py` | `DroughtSource` and its payload Protocols; `pipeline/source_bindings.py` binds the implementation |
| `usdm.py` | `USDM_DROUGHT_SOURCE`: the USDM implementation; a 404 means not yet, never absent |
| `products.py` | the Tuesday release calendar and `drought_lane_registration()` |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

**`products.py` imports `LANE_REGISTRY`**, so `pipeline/parquet/lane_registry.py` must never import
it (or `usdm.py`, which imports it): that edge closes a cycle through a half-initialised registry.
