# `pipeline/lanes/` — one lane per module or package

Every module and every subpackage here is ONE lane: `tests/test_layer_import_contract.py::_lane_names`
names them, and `test_lanes_do_not_import_each_other` fails the moment one lane imports another
(`layer-lanes.md` §1). Shared needs move DOWN the lattice, never beside the lanes.

## Config-lane strategies (track `config_driven_ingestion_20260926`, spec §4.2, S14)

A lane TOML's `strategy = "<layer>.<source>"` resolves, by `importlib` and with no registry, to
`pipeline/lanes/<layer>/<source>.py`, attribute `STRATEGY` (`pipeline/runner/resolve.py`). Adding a
lane is a TOML plus one module. A strategy is thin (D2): `plan_requests`, `fetch`, `settle`, `rows`,
and `probe_edge` where S6 requires it; the runner owns everything else. The Protocols are
`pipeline/runner/contract.py` (a draft until the Phase-2 re-freeze).

A strategy module imports only: `foundation`, `warehouse` (incl. `field_products`), `ingest`,
`pipeline/runner/contract.py` (never any other runner module), `pipeline/parquet`, and its OWN
`pipeline/lanes/<layer>/` package. Never another layer's package, and never `pipeline/direct/**`,
which the Phase-7 cleanup lane deletes. It fetches only through the `ProviderClient` it is handed,
logs through `foundation/observability`, and never calls `print` (FR-38).

**Nothing shared lives here** except `transforms/` and this file (plan 1B). `__init__.py` and
`calendar.py` are the pre-existing Parquet dimension lane and are untouched by the config track.

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
