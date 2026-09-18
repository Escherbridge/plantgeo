"""The drought layer's source contract: coverage, availability walk, and one bounded dated pull.

See `AGENTS.md` in this directory, section "The source protocol", for what the layer owns, what a
source owns, and which part of the record is not normalized yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import date, datetime

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


@runtime_checkable
class DroughtReleaseDay(Protocol):
    """One dated drought release as the layer consumes it: the day, the payload, and when it was read.

    `release` is `None` for a source's own documented "not published yet" answer -- a real answer,
    not a fetch failure, which is what lets the adapter turn it into a governed absence rather than
    a refusal. Timestamps are UTC (`federation.md` §2, "units, datums and calendars normalize at the
    source boundary"); the region's declared local timezone lives in the manifest, not here.
    """

    day: date
    release: object | None
    fetched_at: datetime


@runtime_checkable
class DroughtSource(Protocol):
    """One region's drought source: what it covers, which days it publishes, and how one day is read.

    Implemented by `usdm.py` for the pilot. A second region implements this against its own national
    drought monitor and binds it in the region manifest; nothing in `rows.py`, `adapter.py`,
    `forward.py`, `warehouse/schemas/drought.py` or `planes/drought.py` learns the source's name.
    """

    #: The manifest's `source_slug` for this implementation, e.g. `"usdm"`.
    source_slug: str
    #: Where this source can fill the drought layer at all (`federation.md` §2).
    coverage: SourceCoverageClaim

    def release_days(self, first_day: date, last_day: date) -> tuple[date, ...]:
        """Every day this source publishes a release for in `[first_day, last_day]`, oldest first."""
        ...

    async def fetch_release_day(
        self,
        day: date,
        *,
        retry_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> DroughtReleaseDay:
        """Fetch exactly one dated release, retrying transport failures as one bounded unit."""
        ...


__all__ = [
    "DroughtReleaseDay",
    "DroughtSource",
]
