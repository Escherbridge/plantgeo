"""The `soil-survey` layer's source protocol and its SSURGO binding; no writer lives here yet.

DELIBERATELY EMPTY OF RE-EXPORTS, matching `pipeline/direct/soil/__init__.py` and
`pipeline/direct/climate/__init__.py`: callers import the submodule they mean, so a package
`__init__` can never close an import cycle through `pipeline/parquet/lane_registry.py`.
See `AGENTS.md` in this directory for why the layer has a protocol but no pull.
"""
