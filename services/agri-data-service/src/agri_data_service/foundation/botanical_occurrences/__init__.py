"""Pure botanical-occurrence primitives: limits, identities, terms, event intervals, coordinates.

Layer L0 (foundation): imports nothing from this service outside `foundation`. See `AGENTS.md`.
"""

from __future__ import annotations

from agri_data_service.foundation.botanical_occurrences.limits import (
    ADMITTED_LIMITS,
    PARSER_VERSION,
    QC_POLICY_VERSION,
    SUPPORT_VERSION,
    TAXONOMY_RECIPE_VERSION,
    AcquisitionLimitError,
    AcquisitionLimits,
)

__all__ = [
    "ADMITTED_LIMITS",
    "PARSER_VERSION",
    "QC_POLICY_VERSION",
    "SUPPORT_VERSION",
    "TAXONOMY_RECIPE_VERSION",
    "AcquisitionLimitError",
    "AcquisitionLimits",
]
