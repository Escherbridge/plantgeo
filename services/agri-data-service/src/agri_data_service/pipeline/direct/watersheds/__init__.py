"""Source-direct Parquet writer for the watersheds lane: fetches NHDPlus_HR WBDHU12 directly and
writes Parquet without ever staging a row in PostgreSQL. Deliberately empty of re-exports -- see
`pipeline/parquet/lane_registry.py`'s module docstring, "each package `__init__` is deliberately
empty of re-exports because pulling in a writer would close a cycle back through this file."

STATUS: built, not yet registered. `LANE_REGISTRY[WATERSHEDS_STREAM].adapter` still reads
`geo.features` via `pipeline/lanes/watersheds.py::export_watersheds_release`, and its watermark
resolver (`pipeline/parquet/lane_registry.py::_watersheds_watermark`) still reads `geo.features`
too -- both in a file this package may not edit. Wiring this writer's `forward.py` in as the
registration's adapter AND watermark (or naming this package `PENDING_REGISTRATION` in
`tests/direct/test_direct_package_registration.py`, mirroring `vegetation`/`weather_observations`/
`drought`), and retiring `ingest/watersheds.py::run_watersheds_ingestion_job` / the
`postgres-watersheds` lane that calls it, is the join step's job. See `parity.py`'s module
docstring for the counted receipt that step should read first, and `forward.py`'s module docstring
for why this lane's watermark is read from the SOURCE rather than from Postgres.
"""

from __future__ import annotations
