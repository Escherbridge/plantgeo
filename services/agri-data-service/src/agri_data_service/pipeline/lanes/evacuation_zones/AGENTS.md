# `pipeline/lanes/evacuation_zones/` — the evacuation-zones layer package

Moved verbatim from `pipeline/direct/evacuation_zones/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Evacuation zones".
The `s-evacuation-zones` swarm slice builds the config-lane strategy beside these modules.

| module | what |
|---|---|
| `products.py` | identity constants and the Oregon-only coverage gate; deliberately NO edge to `LANE_REGISTRY` |
| `source.py` | the bounded Oregon OEM fetch (`fetch_evacuation_zones_snapshot`) |
| `support.py` | DuckDB geometry repair to WKB |
| `rows.py` | the Arrow table, natural keys, content digests and part splitting |
| `watermark.py` | the `static_lookup` source watermark `lane_registry.py` imports |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only (plus `pipeline/constants.py` for `LANE_BASE_ZOOM_TIER`, a residual the strategy should drop).
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`lane_registry.py` imports `watermark.py`, which reaches `products`, `rows`, `source` and `support`.
None of them may import `LANE_REGISTRY`; the registration stays in
`pipeline/direct/evacuation_zones/registration.py`.
