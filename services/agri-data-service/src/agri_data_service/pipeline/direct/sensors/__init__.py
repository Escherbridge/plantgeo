"""Direct-to-Parquet writer for `sensors`, NOAA NWS ground-station readings.

DELIBERATELY EMPTY OF RE-EXPORTS, for the same reason `pipeline/direct/climate/__init__.py` and
`pipeline/direct/weather_observations/__init__.py` are: `pipeline/parquet/lane_registry.py` may
come to import a submodule of this package for its floor and lag, so a package `__init__` that
pulled in `forward.py` -- which imports the registry -- would close an import cycle at module load.
Callers import the submodule they mean.

See `pipeline/direct/AGENTS.md`, "Sensors" (owed at the join step -- this package's ownership
boundary did not include that shared file, see `forward.py`'s module docstring for the equivalent
rationale inline), for why this lane's acquisition model (NWS keeps only a ROLLING ~6-day window,
recomputed relative to the run clock, never a fixed archive floor) is closer to
`weather_observations/`'s shape than to `climate/`'s settled-day archive fetch, and why it borrows
the same water-gauges-style merge-append adapter -- adapted here to replace a whole station-day
measurement block rather than refresh one grain at a time. See `rows.py` and `adapter.py`.
"""

from __future__ import annotations
