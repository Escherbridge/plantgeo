"""Direct-to-Parquet writer for `weather-observations`, the Open-Meteo current-conditions side lane.

DELIBERATELY EMPTY OF RE-EXPORTS, for the same reason `pipeline/direct/climate/__init__.py` is:
`pipeline/parquet/lane_registry.py` may come to import a submodule of this package for its floor and
lag, so a package `__init__` that pulled in `forward.py` -- which imports the registry -- would close
an import cycle at module load. Callers import the submodule they mean.

See `pipeline/direct/AGENTS.md`, "Weather observations", for why this lane's acquisition model
(a current-conditions poll with no archive endpoint) is a different shape from climate/soil's
settled-day archive fetch, and why it borrows water-gauges' merge-append adapter instead.
"""
