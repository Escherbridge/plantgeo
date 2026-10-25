# `pipeline/lanes/drought/` — the drought layer package

Moved verbatim from `pipeline/direct/drought/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Drought".
The `s-drought` swarm slice built the config-lane strategy beside these modules (dark: the shipped
TOML stays `executor = "legacy"`; see `usdm_releases.py`'s module docstring for the open gap that
blocks the flip).

| module | what |
|---|---|
| `source_protocol.py` | `DroughtSource` and its payload Protocols; `pipeline/source_bindings.py` binds the implementation |
| `usdm.py` | `USDM_DROUGHT_SOURCE`: the USDM implementation; a 404 means not yet, never absent. **Reserved**: a moved source leaf with no `STRATEGY` (see below) |
| `products.py` | the Tuesday release calendar and `drought_lane_registration()` |
| `geometry.py` | **(s-drought)** the base-rung DuckDB spatial repair chain, duplicated from `pipeline/direct/drought/support.py` (never imported -- this package may not import `pipeline/direct/**`) |
| `usdm_releases.py` | **(s-drought)** `STRATEGY`: `lanes/drought-direct-forward.toml`'s `drought.usdm_releases` strategy -- plan/fetch/settle/rows plus `release_days` (S19); the 404-unsettled and mirrored-past-absence mapping lives here |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet`, `pipeline/runner/contract.py`
(never any other runner module, S14) and this package only. Never another layer's
`pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

**`products.py` imports `LANE_REGISTRY`**, so `pipeline/parquet/lane_registry.py` must never import
it (or `usdm.py`, which imports it): that edge closes a cycle through a half-initialised registry.

**Reserved module names** (`pipeline/lanes/AGENTS.md`, "Extracted source leaves are NOT strategies"):
`usdm.py` is a moved SOURCE module with no `STRATEGY`. The config-lane strategy therefore lives in a
distinct leaf, `usdm_releases.py`, never bolted onto `usdm.py`.

**Known gap, why `drought-direct-forward.toml` ships `executor = "legacy"`**: USDM's per-release
resource is a dated URL PATH segment (`ingest/usdm.py::usdm_source_url`), not a query parameter.
`pipeline/runner/binding.py::ConfigProviderClient` builds every request URL as a provider's declared
(fixed) `path` plus a `?query` string, which cannot express a path segment that varies per request --
the identical gap `lanes/_providers/nasa-firms.toml`'s own TODO names for FIRMS's path-segment API
key. `usdm_releases.py` is otherwise a complete, tested `IngestStrategy` + `ReleaseCalendarStrategy`;
moving to `executor = "config"` needs a provider-client path-segment transport added first (out of
scope for this slice: `pipeline/runner/binding.py` and `ingest/provider_client.py` are framework
files no swarm lane may edit). See `docs/lanes/drought.md` section 8.
