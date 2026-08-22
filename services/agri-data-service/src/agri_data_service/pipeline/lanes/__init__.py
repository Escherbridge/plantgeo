"""Per-stream source pull and Parquet write (L3 `pipeline`).

One module per stream, named by its slug. A lane never imports another lane; shared needs move
down the lattice. See `conductor/code_styleguides/layer-lanes.md` §1.
"""
