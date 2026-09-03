"""The ONE definition of how far back from today a lane's source could have published.

Layer L2 leaf: `foundation` only, so the side that DECLARES a ceiling (`gap_fill`, through
`availability_extension`) and the side that READS one (`parquet_ops/availability_coverage.py`) bind
to the same rule and cannot drift. See `AGENTS.md` in this directory, "the source ceiling".
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.foundation.parquet.lane_contract import LaneNature


class LaneCeilingFacts(Protocol):
    """The two registration facts a source ceiling is computed from, named structurally.

    A Protocol rather than a concrete type because the two callers hold different lane records --
    `LaneRegistration` on the write side, `CensusLane` on the read side -- and a shared base class
    would drag the whole registry into `parquet_ops`.
    """

    @property
    def nature(self) -> LaneNature: ...

    @property
    def publication_lag_days(self) -> int: ...


def allowed_source_ceiling(lane: LaneCeilingFacts, *, today: date) -> date:
    """Return the newest day this lane's SOURCE could have published by `today`.

    This is the horizon a lane's coverage closes against, and it is a claim about the SOURCE, never
    about which writer owns a day: `LaneRegistration.writer_ceiling` bounds what the generic filler
    may take from a dedicated writer and is deliberately NOT applied here, or every lane a forward
    writer owns would declare a ceiling below the days it publishes.

    A `static_lookup` has no time axis: its partition day is a version stamp keyed to a source
    watermark, so nothing after `today` could have been stamped and nothing before it was owed.
    """
    if not nature_has_time_axis(lane.nature):
        return today
    return today - timedelta(days=lane.publication_lag_days)


__all__ = ["LaneCeilingFacts", "allowed_source_ceiling"]
