"""The declarative archive-lane registry: each archive walk's window grid, and the measurement behind every number."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.firms import (
    FIRMS_API_KEY_VARIABLE,
    FIRMS_ARCHIVE_EARLIEST_OBSERVATION,
    FIRMS_ARCHIVE_SOURCE,
)
from agri_data_service.ingest.policy import MAX_MAX_SOURCE_RECORDS
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.ingest.usgs_nwis import USGS_DAILY_VALUES_EARLIEST, USGS_STREAMFLOW_ARCHIVE_SOURCE

if TYPE_CHECKING:
    from collections.abc import Mapping

# The ceiling `policy.resolve_max_source_records` clamps to, and the value every archive lane must walk
# under. Measured 2026-08-06: a 5-day FIRMS July chunk saw 23,651 records and wrote ZERO, because peak
# PNW fire season runs ~21k detections per day. A bitten cap does NOT thin the sample -- `select_writes`
# ranks by observation time and keeps the newest, so what it deletes is the OLDEST days of the chunk,
# whole. Walking at the 10,000 default is therefore not "a smaller sample of every day", it is a silent
# hole in the record for days the run still reports as walked.
ARCHIVE_MAX_SOURCE_RECORDS: Final = MAX_MAX_SOURCE_RECORDS


class LaneSpecificationError(ValueError):
    """Raised when a declared lane's walk shape cannot produce a grid a walk could actually follow."""


class UnknownBackfillLaneError(LookupError):
    """Raised when a stored payload names a lane no registry entry claims."""


def utc_day_start(moment: datetime) -> datetime:
    """Truncate an instant to the UTC day it falls in, so a lane replanned twice in one day plans one grid."""
    if moment.utcoffset() is None:
        raise LaneSpecificationError("a walk boundary must include a timezone")
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class BackfillLane:
    """One archive walk declared rather than coded: which source it walks, how deep, and in what steps."""

    name: str
    source_name: str
    floor: datetime
    chunk_days: int
    window_days: int
    credential_variable: str | None = None
    max_source_records: int = ARCHIVE_MAX_SOURCE_RECORDS

    def __post_init__(self) -> None:
        """Refuse a lane whose declared shape would walk something other than what it says it walks."""
        if not self.name.strip():
            raise LaneSpecificationError("a lane must be named")
        if not self.source_name.strip():
            raise LaneSpecificationError(f"lane {self.name!r} must name the source token it walks")
        if self.floor != utc_day_start(self.floor):
            # The grid is anchored at the floor and every shard is keyed by its bounds as YYYY-MM-DD, so
            # a floor at 06:00 makes two different windows spell the same key and one of them vanishes
            # into `ON CONFLICT (job_run_id, shard_key) DO NOTHING`.
            raise LaneSpecificationError(f"lane {self.name!r} must floor on a UTC midnight")
        if self.chunk_days < 1:
            raise LaneSpecificationError(f"lane {self.name!r} must fetch at least one day per chunk")
        if self.window_days < self.chunk_days:
            # `history_chunks` anchors chunks at the window start with a fixed step and clamps the last
            # one to the window end, so a window narrower than its own chunk yields ONE chunk covering
            # the whole window -- silently walking wider than the lane declares, which is exactly the
            # overrun that wrote zero rows for a 5-day July FIRMS chunk.
            raise LaneSpecificationError(f"lane {self.name!r} must not declare a chunk wider than its window")
        if not 0 < self.max_source_records <= MAX_MAX_SOURCE_RECORDS:
            raise LaneSpecificationError(
                f"lane {self.name!r} must declare a record ceiling within 1..{MAX_MAX_SOURCE_RECORDS}"
            )

    @property
    def chunk(self) -> timedelta:
        """The interval a `BackfillPlan` walks one fetch at a time."""
        return timedelta(days=self.chunk_days)

    @property
    def window(self) -> timedelta:
        """The interval one work item owns, which is what a killed container redoes at worst."""
        return timedelta(days=self.window_days)

    @property
    def floor_day(self) -> str:
        """The floor as the `YYYY-MM-DD` day that names it in a run key."""
        return self.floor.date().isoformat()

    def missing_credential(self) -> str | None:
        """Name the environment variable this lane needs and does not have, read at call time like every other."""
        if self.credential_variable is None:
            return None
        return None if os.environ.get(self.credential_variable, "").strip() else self.credential_variable

    def to_parameters(self) -> dict[str, object]:
        """Render the walk shape as the JSONB an operator reads off `agri.job_definition.parameters`."""
        return {
            "lane": self.name,
            "source": self.source_name,
            "floor": self.floor_day,
            "chunk_days": self.chunk_days,
            "window_days": self.window_days,
            "max_source_records": self.max_source_records,
            "credential_variable": self.credential_variable,
        }


@dataclass(frozen=True, slots=True)
class LaneWindow:
    """One claimable step of a lane's walk: a half-open day span, and its index on the lane's fixed grid."""

    window: HistoryWindow
    grid_index: int

    def __post_init__(self) -> None:
        """Refuse a negative grid index, which would invert the newest-first priority the claim depends on."""
        if self.grid_index < 0:
            raise LaneSpecificationError("a lane window's grid index must not be negative")

    @property
    def start_day(self) -> str:
        """The window's inclusive first day as `YYYY-MM-DD`."""
        return self.window.start.date().isoformat()

    @property
    def end_day(self) -> str:
        """The window's exclusive last day as `YYYY-MM-DD`."""
        return self.window.end.date().isoformat()

    @property
    def label(self) -> str:
        """`START..END`, the same spelling the bash failure ledger used, so an old log line still reads."""
        return f"{self.start_day}..{self.end_day}"


def lane_windows(lane: BackfillLane, end: datetime) -> tuple[LaneWindow, ...]:
    """Expand a lane into every WHOLE window it owes below `end`, NEWEST FIRST, on a grid anchored at the floor.

    Newest first is a CORRECTNESS choice, not a preference. `getSliderCapabilities` does not take the
    time axis from `min(observed_day)`: it keeps only the newest CLUSTER of days separated by no more
    than 45 days, then applies a density floor. Filling forward from the floor would build a second
    cluster years away from the live one and move the axis start by nothing at all until the two finally
    met -- so days of correct work would look like no work, and the walk would be abandoned as broken
    before it ever paid off. Walking backward extends the existing cluster one window at a time and the
    axis grows with every window that lands. The ledger expresses that ordering as `priority`, because
    the claim orders `priority DESC, available_at` and `grid_index` rises toward the present.

    The grid is anchored at the FLOOR and not, as the bash driver did, at "today". A cursor file only had
    to name a boundary, so a grid that shifted every day cost nothing; a set of work items is keyed by
    its shard, so a shifting grid would re-key every window on every replan and the whole archive would
    be planned again from scratch. Anchoring at the floor makes the grid immutable, which is what makes
    re-planning a no-op.

    The trailing days above the newest whole window are DELIBERATELY NOT PLANNED, and that is the other
    half of what makes a replan a no-op. A partial window has to end at the walk boundary, so its start
    sits still while its end moves with the calendar: a NEW shard key every day, inserted alongside
    yesterday's rather than replacing it, because `ON CONFLICT (job_run_id, shard_key) DO NOTHING` has
    nothing to match against. Five replans over one 5-day FIRMS period plan 3+4+5+6+5 = 23 day-walks
    where the grid owes 5, and at the measured 11.5 minutes for a peak-season FIRMS day that is hours of
    duplicated fetch per period. Worse, every one of them carries the HIGHEST `grid_index` on the grid
    and the claim orders `priority DESC` -- so a scheduler that replans on every tick mints a new
    top-priority near-present window faster than the walk retires it, and the 2012 backlog is never
    reached. Keying the partial by its grid index alone does not fix that: `DO NOTHING` would keep the
    FIRST end it was ever planned with, so the shard would silently stop short of the days its key
    claims. Nothing is lost by leaving them out -- the forward hourly cron owns the present, and every
    trailing day is planned as part of a whole window within `window_days` of the boundary passing it.
    """
    boundary = utc_day_start(end)
    if boundary <= lane.floor:
        return ()
    whole_windows = (boundary - lane.floor) // lane.window
    return tuple(
        LaneWindow(
            window=HistoryWindow(
                start=lane.floor + timedelta(days=grid_index * lane.window_days),
                end=lane.floor + timedelta(days=(grid_index + 1) * lane.window_days),
            ),
            grid_index=grid_index,
        )
        for grid_index in range(whole_windows - 1, -1, -1)
    )


FIRMS_ARCHIVE_LANE: Final = BackfillLane(
    name="firms-archive",
    source_name=FIRMS_ARCHIVE_SOURCE,
    # MODIS_SP's own published `min_date`, read from the live availability table on 2026-08-05 and the
    # oldest floor any FIRMS_HISTORY_SOURCES member offers (VIIRS_SNPP_SP starts 2012-01-20); asking for
    # anything older is refused upstream. Owner decision 2026-08-07: the fire layer walks the WHOLE
    # archive rather than the four-year fire-season window the first driver defaulted to, because
    # `fire-detections` is otherwise two dense islands with 31 months of nothing between them.
    floor=FIRMS_ARCHIVE_EARLIEST_OBSERVATION,
    # One day per fetched chunk, ALWAYS. Peak PNW fire season runs ~21k detections/day, so even the
    # 50,000 ceiling is overrun by the 7-day default -- measured 2026-08-06, a 5-day July chunk saw
    # 23,651 records and wrote 0. A too-large chunk is not a slower walk, it is a silent hole.
    chunk_days=1,
    # Small, because peak-season days are slow: one 2026-07-23 chunk took 11.5 minutes. A window is the
    # unit a killed run redoes at worst, so it is kept near an hour of work.
    window_days=5,
    # The key lives only on the cron service; no local .env carries it. The bash read it out of
    # `railway variables` once per walk for the same reason.
    credential_variable=FIRMS_API_KEY_VARIABLE,
)

STREAMFLOW_ARCHIVE_LANE: Final = BackfillLane(
    name="streamflow-archive",
    source_name=USGS_STREAMFLOW_ARCHIVE_SOURCE,
    # The daily-values service reaches the 1890s, but `USGS_DAILY_VALUES_EARLIEST` refuses a walk below
    # this day for the reason given there: it is the vegetation layer's own earliest observed day, which
    # is the axis the slider actually draws, and no other layer can populate the years beneath it.
    floor=USGS_DAILY_VALUES_EARLIEST,
    # Measured 2026-08-07: the full PNW box yields ~807 gauge-days per day, so a 10-day chunk is ~8,070
    # records -- comfortably inside the 50,000 ceiling, and far fewer round trips than FIRMS needs.
    # Daily values are one reading per gauge per day, not a detection cloud.
    chunk_days=10,
    window_days=30,
)

BACKFILL_LANES: Final[Mapping[str, BackfillLane]] = MappingProxyType(
    {lane.name: lane for lane in (FIRMS_ARCHIVE_LANE, STREAMFLOW_ARCHIVE_LANE)}
)


def resolve_lane(name: str) -> BackfillLane:
    """Resolve a lane by name, naming the registry rather than returning None into a walk that writes nothing."""
    lane = BACKFILL_LANES.get(name)
    if lane is None:
        known = ", ".join(sorted(BACKFILL_LANES)) or "nothing"
        raise UnknownBackfillLaneError(f"no backfill lane named {name!r}; registered lanes are {known}")
    return lane
