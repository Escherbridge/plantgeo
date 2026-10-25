# `pipeline/lanes/evacuation_zones/` — the evacuation-zones layer package

Moved verbatim from `pipeline/direct/evacuation_zones/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Evacuation zones".
The `s-evacuation-zones` swarm slice built the config-lane strategy (`oregon_oem.py`) beside these
modules; see that module's own docstring for the design decisions unique to a content-watermarked
`static_lookup`, and `docs/lanes/evacuation-zones.md` section 8 for the lane-level summary.

| module | what |
|---|---|
| `products.py` | identity constants and the Oregon-only coverage gate; deliberately NO edge to `LANE_REGISTRY` |
| `source.py` | the bounded Oregon OEM fetch (`fetch_evacuation_zones_snapshot`) |
| `support.py` | DuckDB geometry repair to WKB |
| `rows.py` | the Arrow table, natural keys, content digests and part splitting |
| `watermark.py` | the `static_lookup` source watermark `lane_registry.py` imports |
| `oregon_oem.py` | the config-lane strategy (S14, `evacuation-zones-direct-forward.toml`); the only module here that imports `pipeline/runner/contract.py` |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only. `oregon_oem.py`
also imports `pipeline/runner/contract.py` (the strategy Protocols, per S14 the one runner module a
strategy may import) and never touches `pipeline/constants.py::LANE_BASE_ZOOM_TIER` — the generic
`ObjectStoreLaneWriter` picks the zoom tier, so the strategy has no reason to.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`lane_registry.py` imports `watermark.py`, which reaches `products`, `rows`, `source` and `support`.
None of them may import `LANE_REGISTRY`; the registration stays in
`pipeline/direct/evacuation_zones/registration.py`.
