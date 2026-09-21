"""The bound land-context source supplies complete current captures and coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
from agri_data_service.pipeline.direct.land_context.source import LandContextSnapshot, fetch_snapshot

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage


@runtime_checkable
class LandContextSource(Protocol):
    @property
    def source_slug(self) -> str: ...

    @property
    def coverage(self) -> SourceCoverageClaim: ...

    async def capture(self, storage: AvailabilityStorage, *, timeout_seconds: float) -> LandContextSnapshot: ...


class BlmLandContextSource:
    """Reviewed OR/WA detail, national Idaho surface, and PNW field jurisdictions."""

    source_slug = "blm_surface_management"
    coverage = SourceCoverageClaim(coverage="regional", iso_country_codes=("US",))

    async def capture(self, storage: AvailabilityStorage, *, timeout_seconds: float) -> LandContextSnapshot:
        return await fetch_snapshot(storage, timeout_seconds=timeout_seconds)


BLM_LAND_CONTEXT_SOURCE: LandContextSource = BlmLandContextSource()
