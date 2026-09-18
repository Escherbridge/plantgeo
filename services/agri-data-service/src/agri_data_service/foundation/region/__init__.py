"""The region manifest sub-package: `federation.md` §1's one typed footprint declaration.

Layer L0 (`foundation`). Imports only `pydantic` and stdlib -- criteria 1-2 of `foundation/AGENTS.md`'s
Admission Test. Criteria 3 ("used by two or more layers") and 4 ("name describes a mechanism, not a
domain noun") do NOT hold here: `region` is the product's central domain noun, and this package
placed itself at L0 anyway per `federation.md` §1 and `python.md` "Readability and region
portability", which both name `foundation/region/` as the manifest's home regardless of the general
test. This is a documented exception to those two criteria and to the "no I/O" responsibility
(`load_region()` reads `PLANTGEO_REGION` and, on a cache miss, `<slug>.json`), not a claim of
conformance -- see `foundation/AGENTS.md` for the same note from the other side.

`load_region()` is the only door: no module-level `Region` constant is exported, so a caller cannot
reach for a hidden dependency on the pilot's footprint instead of taking a `Region` parameter or
calling this function explicitly (`federation.md` §1; `python.md` "the region is a value, not a
constant").
"""

from agri_data_service.foundation.region.manifest import (
    LatticeOriginRule,
    LayerBinding,
    Region,
    RegionEnvelope,
    SourceCoverage,
    load_region,
)

__all__ = [
    "LatticeOriginRule",
    "LayerBinding",
    "Region",
    "RegionEnvelope",
    "SourceCoverage",
    "load_region",
]
