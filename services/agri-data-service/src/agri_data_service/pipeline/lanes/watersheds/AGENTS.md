# `pipeline/lanes/watersheds/` — the watersheds layer package

Moved verbatim from `pipeline/direct/watersheds/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Watersheds".
The `s-watersheds` swarm slice (below) built the config-lane strategy beside these modules.

| module | what |
|---|---|
| `source.py` | the NHDPlus HR WBDHU12 fetch and its `WatershedRecord` identity (p4-extract) |
| `watermark.py` | the `max(loaddate)` source watermark `lane_registry.py` imports (p4-extract) |
| `nhdplus.py` | the `watersheds.nhdplus` config-lane `STRATEGY` (s-watersheds): wraps `source.py`/`watermark.py` in the runner contract, dark behind `watersheds-direct-forward.toml`'s `executor = "legacy"` |
| `geometry.py` | `nhdplus.py`'s own DuckDB spatial GeoJSON-to-WKB conversion -- a deliberate duplicate of `pipeline/direct/watersheds/support.py`'s, never an import of it (see its own module docstring) |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`lane_registry.py` imports `watermark.py`, so neither module may import `LANE_REGISTRY`.

**`nhdplus.py` is a strategy, not a moved leaf** (`pipeline/lanes/AGENTS.md` "Extracted source
leaves are NOT strategies" and "Reserved module names"): it is the ONLY module here with a
`STRATEGY` attribute, and `source.py`/`watermark.py` keep none. It never imports
`pipeline/direct/**` either -- its own fetches go through `source.py`'s `upstream_client`-backed
`fetch_watersheds_snapshot`, never the turn's governed `ProviderClient` (Wave O's named
alternative); see `nhdplus.py`'s module docstring for the full cost-model reasoning.
