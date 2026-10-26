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
| `geometry.py` | **(s-drought)** the base-rung DuckDB spatial repair: `_shared/geometry.py`'s `"always_multi"` chain mode, byte-identical to `pipeline/direct/drought/support.py` (never imported -- this package may not import `pipeline/direct/**`) |
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

**Status: both gaps that used to block `executor = "config"` are closed; the TOML still ships
`executor = "legacy"` on the `p5-cutover` G6 flip's own schedule, not a remaining technical blocker.**

- **Path-segment transport landed (wave B2) and `usdm_releases.py` now uses it.** USDM's per-release
  resource is a dated URL PATH segment (`ingest/usdm.py::usdm_source_url`), which used to have no
  declared-endpoint shape to express (the identical gap `lanes/_providers/nasa-firms.toml`'s own TODO
  still names for FIRMS's path-segment API key). `foundation/lane_config/models.py::ProviderEndpoint`
  now declares `path_template`, and `lanes/_providers/usdm.toml`'s `[endpoints.release]` uses it
  (`path_template = "/data/json/usdm_{release}.json"`). `usdm_releases.py::UsdmReleaseSeriesStrategy
  .plan_requests` plans `RELEASE_ENDPOINT = "release"` with a `release` parameter
  (`day.strftime("%Y%m%d")`), which `ProviderEndpoint.split_parameters` routes into the path rather
  than the query -- rendering the IDENTICAL URL `ingest/usdm.py::usdm_source_url` builds for the
  legacy writer, byte-for-byte.
- **`ingested_at` handled via `volatile_columns` (wave B1a).** `_source_digest` (`usdm_releases.py`)
  excludes `ingested_at` from this strategy's OWN S11 revision digest -- a different digest over a
  different input than G6's `--mode forward --compare`, which diffs
  `pipeline/runner/turn.py::_Turn._compare`'s `reader.canonical_digest(stream, built)` against
  `reader.canonical_digest(stream, published)`. That path is now volatile-column-aware:
  `drought-direct-forward.toml`'s `[[streams]]` entry declares `volatile_columns = ["ingested_at"]`,
  and `canonical_digest` drops the declared columns after conforming and grain-sorting
  (`pipeline/runner/AGENTS.md` "Volatile columns"), so neither digest sees a false delta on an
  already-settled day. Not drought-specific: any lane whose schema carries a retrieval-instant column
  gets the identical protection once its stream declares `volatile_columns`.

See `docs/lanes/drought.md` section 8 for the lane-level summary.
