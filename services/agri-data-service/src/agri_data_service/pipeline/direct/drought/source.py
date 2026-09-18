"""DEPRECATED re-export shim: this module moved to `usdm.py`; delete once every importer is repointed.

Kept so `federation.md` §5 step 3 changes no importer, CLI verb, lane spec or test path in the same
push that introduces the protocol. `AGENTS.md` in this directory lists it as wave-4 deletion work.
"""

from __future__ import annotations

from agri_data_service.pipeline.direct.drought.usdm import (
    DroughtDaySource,
    DroughtSourceError,
    fetch_drought_day,
)

__all__ = [
    "DroughtDaySource",
    "DroughtSourceError",
    "fetch_drought_day",
]
