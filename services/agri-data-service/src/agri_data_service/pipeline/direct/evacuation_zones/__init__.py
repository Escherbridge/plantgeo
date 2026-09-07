"""Direct-to-Parquet Oregon OEM evacuation-zone writer: source, geometry repair, forward publication.

Replaced `pipeline/lanes/evacuation_zones.py::export_evacuation_zones_day` (which read
`geo.features` and LEFT JOINed `geo.geometry`); THAT MODULE AND ITS SQL WERE DELETED ON 2026-09-06.
It also replaces `ingest/evacuation_zones.py::run_evacuation_zones_ingestion_job`, which WROTE those
rows and WHICH IS STILL LIVE: `ingest/runner.py:48` keeps it in the `ingest-all` macro, so the
Postgres producer is retained with that blocker named (see
`conductor/tracks/environmental_postgres_retirement_20260904/evidence/removal-packet-watersheds-evacuation-zones-20260906.md`).
Neither this package nor anything it imports writes PostgreSQL; `parity.py` is the only module that
reads it.

STATUS: REGISTERED since 2026-09-06, both halves in one edit. `LANE_REGISTRY['evacuation-zones']`
carries a source-direct refusal naming this package as its adapter and `watermark.py` as its
watermark, and `sql/pipeline/lane_watermark_evacuation_zones.sql` was deleted in the same edit. The
watermark half was a DESIGN DECISION rather than a transcription -- Oregon publishes no column that
dates a change -- and `watermark.py`'s module docstring is where that decision is argued. Two modules
exist only to keep the registry importable: `registration.py` holds this package's single edge back
to `LANE_REGISTRY`, and `products.py` deliberately holds none.

See `pipeline/direct/AGENTS.md` for the shared conventions and this package's own section for what
the retired `geo.geometry` join actually contributed and how each of its three columns is reproduced.
"""

from __future__ import annotations
