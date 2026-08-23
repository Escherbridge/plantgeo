"""Per-lane reconciliation of written Parquet against the SOURCE SYSTEM (L3 `pipeline`).

One module per stream, named by slug. Validation lives here rather than in `method` because it
needs the network: comparing a lane against its own intermediate state only proves the code
agrees with itself. A lane never imports another lane -- enforced by
`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`.
See `conductor/code_styleguides/layer-lanes.md` §4.
"""
