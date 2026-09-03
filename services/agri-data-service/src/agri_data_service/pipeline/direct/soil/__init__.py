"""The direct ERA5-Land soil writer: eight streams, three browser products, one lock each.

DELIBERATELY EMPTY OF RE-EXPORTS, for the same reason `pipeline/direct/climate/__init__.py` is:
`pipeline/parquet/lane_registry.py` imports `products.py` for the floors and lags, so a package
`__init__` that pulled in `forward.py` -- which imports the registry -- would close an import cycle
at module load. Callers import the submodule they mean.
"""
