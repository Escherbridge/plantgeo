"""A source's own coverage claim: `global`, or `regional` with the ISO country codes it serves.

See `AGENTS.md` in this directory, section "Source coverage claims", for why a source's country
list is not a footprint literal and why containment is checked on ISO codes rather than geometry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, model_validator

from agri_data_service.foundation.region.manifest import SourceCoverage

if TYPE_CHECKING:
    from agri_data_service.foundation.region.manifest import Region


class SourceCoverageClaim(BaseModel):
    """What ground one source can fill a layer over; `federation.md` §2's coverage declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    coverage: SourceCoverage
    #: ISO 3166-1 alpha-2 codes, empty for a global source. A source's own reach, not a footprint.
    iso_country_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _a_claim_names_exactly_the_countries_its_coverage_implies(self) -> SourceCoverageClaim:
        if self.coverage == "regional" and not self.iso_country_codes:
            raise ValueError("a regional source must name the ISO country codes it serves")
        if self.coverage == "global" and self.iso_country_codes:
            raise ValueError("a global source serves every country and must not list ISO codes")
        return self

    def covers_region(self, region: Region) -> bool:
        """True when this source reaches every ISO country the region's manifest claims ground in."""
        if self.coverage == "global":
            return True
        return set(region.iso_country_codes).issubset(self.iso_country_codes)

    def uncovered_country_codes(self, region: Region) -> tuple[str, ...]:
        """The region's ISO codes this source does not serve, sorted -- empty when it covers them all."""
        if self.coverage == "global":
            return ()
        return tuple(sorted(set(region.iso_country_codes) - set(self.iso_country_codes)))


#: The claim a source that serves the whole planet makes; every global source reuses this value.
GLOBAL_SOURCE_COVERAGE: Final = SourceCoverageClaim(coverage="global")


__all__ = [
    "GLOBAL_SOURCE_COVERAGE",
    "SourceCoverageClaim",
]
