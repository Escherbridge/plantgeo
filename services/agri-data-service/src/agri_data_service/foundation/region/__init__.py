"""The region manifest sub-package: `federation.md` §1's one typed footprint declaration.

Layer L0 (`foundation`). Imports only `pydantic` and stdlib; imports no other first-party module,
per this package's own admission test in `foundation/AGENTS.md`.
"""

from agri_data_service.foundation.region.manifest import (
    PNW,
    LatticeOriginRule,
    LayerBinding,
    Region,
    RegionEnvelope,
    SourceCoverage,
    load_region,
)

__all__ = [
    "PNW",
    "LatticeOriginRule",
    "LayerBinding",
    "Region",
    "RegionEnvelope",
    "SourceCoverage",
    "load_region",
]
