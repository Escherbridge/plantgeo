"""Direct-to-Parquet Oregon OEM evacuation-zone writer: source, geometry repair, forward publication.

Replaces `pipeline/lanes/evacuation_zones.py::export_evacuation_zones_day` (the registered
`_fill_evacuation_zones` adapter, which reads `geo.features` and LEFT JOINs `geo.geometry`) and
`ingest/evacuation_zones.py::run_evacuation_zones_ingestion_job` (which writes them). Neither this
package nor anything it imports writes PostgreSQL; `parity.py` is the only module that reads it.

See `pipeline/direct/AGENTS.md` for the shared conventions and this package's own section for what
the retired `geo.geometry` join actually contributed and how each of its three columns is reproduced.
"""

from __future__ import annotations
