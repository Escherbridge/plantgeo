"""DEPRECATED re-export shim: this module moved to `mtbs.py`; delete once every importer is repointed.

Kept so `federation.md` §5 step 3 changes no importer, CLI verb, lane spec or test path in the same
push that introduces the protocol. `AGENTS.md` in this directory lists it as wave-4 deletion work.
"""

from __future__ import annotations

from agri_data_service.pipeline.direct.burn_severity.mtbs import (
    BurnSeverityDaySource,
    BurnSeverityFetchError,
    fetch_burn_severity_release_day,
)

__all__ = [
    "BurnSeverityDaySource",
    "BurnSeverityFetchError",
    "fetch_burn_severity_release_day",
]
