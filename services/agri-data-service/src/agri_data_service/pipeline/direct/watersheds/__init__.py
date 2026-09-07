"""Source-direct Parquet writer for the watersheds lane: fetches NHDPlus_HR WBDHU12 directly and
writes Parquet without ever staging a row in PostgreSQL. Deliberately empty of re-exports -- see
`pipeline/parquet/lane_registry.py`'s module docstring, "each package `__init__` is deliberately
empty of re-exports because pulling in a writer would close a cycle back through this file."

STATUS: REGISTERED since 2026-09-06, both halves in one edit. `LANE_REGISTRY[WATERSHEDS_STREAM]` now
carries a source-direct refusal naming this package as its adapter, and `watermark.py` as its
watermark; `sql/pipeline/lane_watermark_watersheds.sql` was deleted in the same edit. Nothing in the
registry reads `geo.features` for this layer any more, which is what makes
`ingest/watersheds.py::run_watersheds_ingestion_job` and the `postgres-watersheds` lane that calls it
deletable -- see `parity.py` for the counted receipt a removal packet should read first.

The two fields had to move TOGETHER and could not be sequenced: `_watersheds_watermark` read
`geo.features`, which `postgres-watersheds` was the only writer of, so swapping the adapter alone
would have keyed a source-direct writer to a clock that stops advancing, and swapping the watermark
alone would have left the Postgres export publishing under a version day it did not produce.
`forward.py`'s module docstring records why the source's own `loaddate` is the honest replacement.
"""

from __future__ import annotations
