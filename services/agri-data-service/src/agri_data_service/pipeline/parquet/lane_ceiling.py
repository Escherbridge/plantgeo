"""The ONE definition of how far back from today a lane's source could have published.

Layer L2 leaf: `foundation` only, so the side that DECLARES a ceiling (`gap_fill`, through
`availability_extension`) and the side that READS one (`parquet_ops/availability_coverage.py`) bind
to the same rule and cannot drift. See `AGENTS.md` in this directory, "the source ceiling".

A probed provider edge (spec S6: the newest day the provider actually serves values for) is
preferred over `today - publication_lag_days` whenever the caller has one; `SourceCeiling` says
which of the two answered, so the lag tautology is never silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.foundation.parquet.lane_contract import LaneNature

#: Where a ceiling came from: a probed provider edge, the registered lag, or (a static lane) today.
CeilingEdgeSource = Literal["probe", "lag_fallback", "no_time_axis"]

#: The spec S6 edge-source vocabulary, shared with the runner's `ProviderEdge`.
PROBE_EDGE_SOURCE: Final = "probe"
LAG_FALLBACK_EDGE_SOURCE: Final = "lag_fallback"
NO_TIME_AXIS_EDGE_SOURCE: Final = "no_time_axis"


class LaneCeilingFacts(Protocol):
    """Registration facts shared by writer and reader ceiling calculations."""

    @property
    def nature(self) -> LaneNature: ...

    @property
    def publication_lag_days(self) -> int: ...

    @property
    def release_days(self) -> tuple[date, ...] | None: ...


@dataclass(frozen=True, slots=True)
class SourceCeiling:
    """The newest day a lane's source could have published by today, and which edge decided it."""

    day: date
    edge_source: CeilingEdgeSource


def resolve_source_ceiling(lane: LaneCeilingFacts, *, today: date, probed_edge: date | None = None) -> SourceCeiling:
    """Return the newest day this lane's SOURCE could have published by `today`, preferring a probed edge.

    This is the horizon a lane's coverage closes against, and it is a claim about the SOURCE, never
    about which writer owns a day: `LaneRegistration.writer_ceiling` bounds what the generic filler
    may take from a dedicated writer and is deliberately NOT applied here, or every lane a forward
    writer owns would declare a ceiling below the days it publishes.

    A `static_lookup` has no time axis: its partition day is a version stamp keyed to a source
    watermark, so nothing after `today` could have been stamped and nothing before it was owed, and a
    probed edge means nothing to it. A probed edge after `today` is clamped to `today`: no observed
    day is published before it happens. A release calendar snaps whichever edge answered down to the
    newest release at or before it.
    """
    if not nature_has_time_axis(lane.nature):
        return SourceCeiling(day=today, edge_source=NO_TIME_AXIS_EDGE_SOURCE)
    if probed_edge is None:
        closing_day = today - timedelta(days=lane.publication_lag_days)
        edge_source: CeilingEdgeSource = LAG_FALLBACK_EDGE_SOURCE
    else:
        closing_day = min(probed_edge, today)
        edge_source = PROBE_EDGE_SOURCE
    if lane.release_days is not None:
        closing_day = max((day for day in lane.release_days if day <= closing_day), default=closing_day)
    return SourceCeiling(day=closing_day, edge_source=edge_source)


def allowed_source_ceiling(lane: LaneCeilingFacts, *, today: date, probed_edge: date | None = None) -> date:
    """Return the day `resolve_source_ceiling` settles on, for callers that need only the day."""
    return resolve_source_ceiling(lane, today=today, probed_edge=probed_edge).day


__all__ = [
    "LAG_FALLBACK_EDGE_SOURCE",
    "NO_TIME_AXIS_EDGE_SOURCE",
    "PROBE_EDGE_SOURCE",
    "CeilingEdgeSource",
    "LaneCeilingFacts",
    "SourceCeiling",
    "allowed_source_ceiling",
    "resolve_source_ceiling",
]
