"""The soil-survey layer's source contract: coverage, the vintage watermark, and one full re-export.

See `AGENTS.md` in this directory, section "The source protocol", for why this layer's contract is
shaped around a version stamp rather than a calendar day, and why the pilot's implementation of the
pull is absent rather than stubbed silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agri_data_service.foundation.geography.bounding_box import BoundingBox

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


@runtime_checkable
class SoilSurveyRelease(Protocol):
    """One full soil-survey re-export as the layer consumes it, stamped at the source's own vintage.

    `vintage_day` is a VERSION STAMP, not an observation day (`layer-lanes.md` §1a,
    `static_lookup`): a soil delineation is reference data the source republishes, so a partition
    dated at the run date would launder the polling clock into the version. `survey_area_symbols`
    names the source's own area partitioning, which the reconciliation in
    `pipeline/validation/soil_survey.py` counts delineations per. Timestamps are UTC
    (`federation.md` §2).
    """

    #: Read-only properties, not plain attributes: a frozen dataclass implementation's fields are
    #: themselves read-only, and mypy's Protocol structural check requires a plain attribute to be
    #: settable, so a settable-attribute Protocol member can never be satisfied by a frozen
    #: dataclass field even when the types match exactly.
    @property
    def vintage_day(self) -> date:
        """The source's own version stamp this release was exported at."""
        ...

    @property
    def survey_area_symbols(self) -> tuple[str, ...]:
        """The source's own area partitioning this release covers."""
        ...

    @property
    def records(self) -> tuple[object, ...]:
        """Read-only so a concrete source may narrow this to its own record payload type."""
        ...

    @property
    def fetched_at(self) -> datetime:
        """When this release was read, UTC."""
        ...


@runtime_checkable
class SoilSurveySource(Protocol):
    """One region's soil-survey source: coverage, its own change watermark, and a full re-export pull.

    `ssurgo.py` is the pilot's binding. Nothing in `warehouse/schemas/soil_survey.py`,
    `pipeline/validation/soil_survey.py` or `planes/soil_survey.py` may branch on which source
    filled the layer; a national soil survey elsewhere implements this protocol and binds itself in
    that region's manifest.
    """

    #: The manifest's `source_slug` for this implementation, e.g. `"ssurgo"`.
    source_slug: str
    #: Where this source can fill the soil-survey layer at all (`federation.md` §2).
    coverage: SourceCoverageClaim

    async def source_vintage_watermark(self) -> date:
        """The source's own "when did this last change" day -- a change event, never a poll clock.

        `layer-lanes.md` §1a: a `static_lookup` declares a source watermark, and a column a re-fetch
        of unchanged ground advances is forbidden as one. A partition dated at or after this day is
        current; otherwise exactly one snapshot is owed, dated AT the watermark.
        """
        ...

    async def fetch_release(
        self,
        *,
        bounding_box: BoundingBox,
        survey_area_symbols: Sequence[str],
    ) -> SoilSurveyRelease:
        """Re-export the whole published soil survey inside the envelope, as one release.

        There is no per-day pull for a `static_lookup`: a release is the entire set at one vintage,
        which is why this takes no day argument. The envelope is the region manifest's, passed in
        (`layer-lanes.md` §1b).
        """
        ...


__all__ = [
    "BoundingBox",
    "SoilSurveyRelease",
    "SoilSurveySource",
]
