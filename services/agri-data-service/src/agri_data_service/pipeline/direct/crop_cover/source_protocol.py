"""The region-bound annual crop source declares US coverage and captures classified pixels."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
from agri_data_service.pipeline.direct.crop_cover.source import capture

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.pipeline.direct.crop_cover.source import CaptureConfig


@runtime_checkable
class CropCoverSource(Protocol):
    """A crop source must declare its coverage and preserve a complete annual capture."""

    @property
    def source_slug(self) -> str: ...

    @property
    def coverage(self) -> SourceCoverageClaim: ...

    def capture(self, config: CaptureConfig) -> Path: ...


class UsdaCropCoverSource:
    """Public USDA annual CDL classifications, bounded to admitted PNW captures."""

    source_slug = "usda_cdl"
    coverage = SourceCoverageClaim(coverage="regional", iso_country_codes=("US",))

    def capture(self, config: CaptureConfig) -> Path:
        return capture(config)


USDA_CROP_COVER_SOURCE: CropCoverSource = UsdaCropCoverSource()
