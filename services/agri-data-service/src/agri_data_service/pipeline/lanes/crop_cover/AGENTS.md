# `pipeline/lanes/crop_cover/` — the crop-cover layer package

Moved verbatim from `pipeline/direct/crop_cover/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/crop_cover/AGENTS.md`.
The `s-crop-cover` swarm slice built the config-lane strategy beside these modules (`usda_cdl.py`)
and, as its own Wave O item, moved `source.py`'s two raw `httpx.Client` sends onto the metered
factory; see that module's own history for the "why" rather than restating it here.

| module | what |
|---|---|
| `products.py` | the CDL service, grids, crop codes and the region's capture envelope |
| `source.py` | the bounded CDL capture, sent through `ingest/http.py`'s metered `upstream_sync_client` (Wave O; no raw-client allow-list entry) |
| `source_protocol.py` | `CropCoverSource` and `USDA_CROP_COVER_SOURCE` for `pipeline/source_bindings.py` |
| `usda_cdl.py` | the `crop_cover.usda_cdl` config-lane strategy (`crop-cover-usda-maintain.toml`, dark); proves edition identity only, never a tile |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only (plus `pipeline/errors.py`, a residual the strategy should drop).
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.
