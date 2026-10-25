# `pipeline/lanes/` — one lane per module or package

Every module and every subpackage here is ONE lane: `tests/test_layer_import_contract.py::_lane_names`
names them, and `test_lanes_do_not_import_each_other` fails the moment one lane imports another
(`layer-lanes.md` §1). Shared needs move DOWN the lattice, never beside the lanes -- with ONE
sanctioned exception, `_shared/` (below), which is a library, not a lane.

## Config-lane strategies (track `config_driven_ingestion_20260926`, spec §4.2, S14)

A lane TOML's `strategy = "<layer>.<source>"` resolves, by `importlib` and with no registry, to
`pipeline/lanes/<layer>/<source>.py`, attribute `STRATEGY` (`pipeline/runner/resolve.py`). Adding a
lane is a TOML plus one module. A strategy is thin (D2): `plan_requests`, `fetch`, `settle`, `rows`,
and `probe_edge` where S6 requires it; the runner owns everything else. The Protocols are
`pipeline/runner/contract.py` (a draft until the Phase-2 re-freeze).

A strategy module imports only: `foundation`, `warehouse` (incl. `field_products`), `ingest`,
`pipeline/runner/contract.py` (never any other runner module), `pipeline/parquet`,
`pipeline/lanes/_shared` (the shared lane library), and its OWN `pipeline/lanes/<layer>/` package.
Never another layer's package, and never `pipeline/direct/**`, which the Phase-7 cleanup lane
deletes. It fetches only through the `ProviderClient` it is handed, logs through
`foundation/observability`, and never calls `print` (FR-38).

**Nothing shared lives here** except `transforms/`, `_shared/` and this file (plan 1B). `__init__.py`
and `calendar.py` are the pre-existing Parquet dimension lane and are untouched by the config track.

## `_shared/` — the lane library (framework-adjacent, not a lane)

The owner's direction for the config pipeline is one library of functions rewritten out of the
legacy lanes and reused by every strategy. `_shared/` is that library: the DuckDB spatial session and
named geometry repair chains, the canonical-JSON digest pair, and the ArcGIS envelope verdict. Any
lane may import it; it imports no lane (only `foundation` and third-party), and it holds NO
lane-specific logic -- no lane's refusal policy, exception type, resource caps, field exclusions or
error messages. Those stay at the call site and are passed in as parameters. Rules and rationale:
`_shared/AGENTS.md`. The leading underscore keeps S14 from ever resolving a strategy there.

`tests/test_layer_import_contract.py::_sibling_module_violations` exempts `_shared` as an import
TARGET via `SHARED_LANE_LIBRARIES` (it still polices `_shared` importing a lane).

A stream's capture-time columns (`ingested_at` and kin) are declared in the lane TOML's `[[streams]]`
`volatile_columns` so G6 `--compare` ignores them; see `pipeline/runner/AGENTS.md` "Volatile columns".

## `transforms/`

Generic transform strategies (D4), resolved by S14 like any other (`strategy = "transforms.<name>"`).
A transform imports only `pipeline/runner/contract.py`, reads its inputs' published partitions
through the runner, and no lane imports it. `transforms/` is itself a lane to the import-contract
test, which is exactly why nothing may import it.

### `transforms/precedence.py`

S7: settled ▷ provisional. The transform lane's `inputs` order is the precedence order. For each
output stream and each input lane, `route_input_stream` picks the one input stream that feeds it:

1. an input stream that is the output slug with ONE hyphen token added
   (`meteorology-era5-dew-point` feeds `meteorology-dew-point`); else
2. the one input stream sharing the output's first token (`shortwave-power` and `shortwave-ifs` feed
   `shortwave-radiation`); else that input lane does not feed the output.

Two candidates under either rule is refused (`PrecedenceRouteError`, the day's `strategy_error`),
never guessed. Rows merge by key (the first of `cell_id`, `station_id`, `site_id` every input
carries): each key takes the highest-ranked input's row, and every row gains
`precedence_source` = the input lane id it came from, so the seam is visible per row (spec §6.2).
An input stream whose every row a higher input already covers is reported in
`Derivation.superseded_inputs`; the runner prunes it only when the transform lane's `[pruning]` is
enabled (S12, a gate flip) and only for a `write_and_recheck` input lane (§4.6).

The routing rule is a naming convention the spec §6.1 slugs satisfy; if a future transform's slugs
do not, the Phase-2 re-freeze should move routing into the lane TOML rather than widen the rule.

## Conformance fixtures

`tests/runner/fixtures/` holds one fixture strategy per nature (FR-3): a grid `refuse` lane with
`probe_edge`, a point `write_and_recheck` lane, a `release_series`, a `static_lookup` read at its
watermark; the fifth, the precedence transform, is this directory's real one. They resolve through
the same S14 resolver (`resolve_strategy(package="tests.runner")`) and are driven through
`run_turn` in `tests/runner/test_conformance.py`.

## Extracted source leaves are NOT strategies (p4-extract, 2026-10-24)

The modules p4-extract moved in from `pipeline/direct/**` (`burn_severity/{source_protocol,mtbs,
products}.py`, `drought/{source_protocol,usdm,products}.py`, `evacuation_zones/*`, `watersheds/*`,
`sensors/merge.py`, `crop_cover/*`, `land_context/*`) are shared source/product leaves, not
strategies: they predate the ProviderClient rule and may still use `upstream_client`, `pipeline.
errors`, `pipeline.constants` or (crop-cover, allow-listed) raw `httpx`. The s-* slices decide per
lane what migrates onto the provider client.

**Reserved module names:** `drought/usdm.py` and `burn_severity/mtbs.py` are moved SOURCE modules
with no `STRATEGY`. A lane TOML's `strategy = "<layer>.<leaf>"` resolves to
`pipeline/lanes/<layer>/<leaf>.py::STRATEGY`, so the s-drought / s-burn-severity strategies must use
a distinct leaf name (e.g. `drought.usdm_releases`), never bolt a `STRATEGY` onto the source module.
