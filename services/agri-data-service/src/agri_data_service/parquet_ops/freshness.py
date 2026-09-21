"""Independent lane freshness: the horizon the PROVIDER should have reached, measured without the pointer.

`source_ceiling_day` is written by the publisher and only advances when a day is published, so a
stalled publisher reports a ceiling that agrees with its own newest day and looks healthy. This
module derives a second horizon from the lane's REGISTERED publication lag and today's date alone,
and states how far publication sits behind it. See `AGENTS.md`, "Independent freshness".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.parquet_ops.wire import render_day, render_instant
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.parquet_ops.coverage import CensusLane
    from agri_data_service.parquet_ops.wire import LaneCoverage, WarehouseCoverage

#: The freshness report's own contract version. It is NOT the frozen `/api/v1/parquet` coverage
#: contract; a consumer that wants these fields on that wire needs the schema bump named in `AGENTS.md`.
FRESHNESS_SCHEMA_VERSION: Final = 1

#: Where the expected horizon comes from: the lane's registered lag, never the availability pointer.
EXPECTED_HORIZON_BASIS: Final = "registered_publication_lag"

#: Days a lane may publish behind its expected horizon, on top of one cadence period, before it is
#: flagged `behind_provider`. Shared with `availability_coverage.AVAILABILITY_STALE_GRACE_DAYS`, which
#: judges the POINTER against the allowed ceiling with the same grace; one literal, two questions.
PUBLICATION_GRACE_DAYS: Final = 3


@dataclass(frozen=True, slots=True)
class LaneFreshness:
    """How far one lane's publication sits behind the horizon its provider's lag says it should have reached."""

    #: `today - publication_lag_days`; `None` for a lane with no time axis, whose day is a version stamp.
    expected_horizon_day: date | None
    #: `expected_horizon_day - latest_recorded_day`, floored at zero; `None` when nothing was recorded.
    staleness_days: int | None
    #: `staleness_days > tolerance_days`; `None` exactly when `staleness_days` is `None`.
    behind_provider: bool | None
    #: `cadence_days + PUBLICATION_GRACE_DAYS`: the slack one publication period plus grace allows.
    tolerance_days: int


def measure_lane_freshness(lane: CensusLane, *, latest_recorded_day: date | None, today: date) -> LaneFreshness:
    """Judge one lane's newest RECORDED day against the horizon its registered lag predicts for today.

    The recorded day, never the carried edge: a bounded-carry release lane answers past its ceiling
    by design, and weighing the carry here would report a healthy lane as ahead of its provider.
    """
    tolerance = lane.cadence_days + PUBLICATION_GRACE_DAYS
    if not nature_has_time_axis(lane.nature):
        return LaneFreshness(
            expected_horizon_day=None, staleness_days=None, behind_provider=None, tolerance_days=tolerance
        )
    expected = allowed_source_ceiling(lane, today=today)
    if latest_recorded_day is None:
        return LaneFreshness(
            expected_horizon_day=expected, staleness_days=None, behind_provider=None, tolerance_days=tolerance
        )
    staleness = max(0, (expected - latest_recorded_day).days)
    if lane.nature == "release_series" and lane.cadence_days == 1 and lane.release_days is None:
        # An irregular release cohort (burn-severity: MTBS releases years apart, registered cadence 1)
        # has no rhythm to be behind; staleness is still stated, but flagging it would keep the
        # operator's short list permanently noisy.
        return LaneFreshness(
            expected_horizon_day=expected, staleness_days=staleness, behind_provider=None, tolerance_days=tolerance
        )
    return LaneFreshness(
        expected_horizon_day=expected,
        staleness_days=staleness,
        behind_provider=staleness > tolerance,
        tolerance_days=tolerance,
    )


def with_freshness(row: LaneCoverage, *, lane: CensusLane, today: date) -> LaneCoverage:
    """Attach the independent verdict and optional registered slider timing policy."""
    verdict = measure_lane_freshness(lane, latest_recorded_day=row.latest_recorded_day, today=today)
    return replace(
        row,
        expected_horizon_day=verdict.expected_horizon_day,
        staleness_days=verdict.staleness_days,
        behind_provider=verdict.behind_provider,
        refresh_policy=lane.refresh_policy,
    )


def render_lane_freshness(row: LaneCoverage) -> dict[str, object]:
    """Render one rung row's freshness beside the two ceilings a consumer needs to read it."""
    return {
        "layer": row.layer,
        "kind": row.kind,
        "zoom": row.zoom,
        "latest_recorded_day": None if row.latest_recorded_day is None else render_day(row.latest_recorded_day),
        "source_ceiling_day": None if row.source_ceiling_day is None else render_day(row.source_ceiling_day),
        "expected_horizon_day": None if row.expected_horizon_day is None else render_day(row.expected_horizon_day),
        "staleness_days": row.staleness_days,
        "behind_provider": row.behind_provider,
        "withheld_reason": row.withheld_reason,
    }


def render_freshness_report(coverage: WarehouseCoverage) -> dict[str, object]:
    """Render the whole census's freshness as a sibling payload to the frozen coverage envelope."""
    return {
        "freshness_schema_version": FRESHNESS_SCHEMA_VERSION,
        "expected_horizon_basis": EXPECTED_HORIZON_BASIS,
        "generated_at": render_instant(coverage.generated_at),
        "evaluated_through_day": render_day(coverage.evaluated_through_day),
        "lanes": [render_lane_freshness(row) for row in coverage.lanes],
    }


def lanes_behind_provider(rows: Sequence[LaneCoverage]) -> tuple[LaneCoverage, ...]:
    """Return the rows flagged behind their provider, in the order given; the operator's short list."""
    return tuple(row for row in rows if row.behind_provider)


def horizon_gap_days(row: LaneCoverage) -> int | None:
    """How far the POINTER's ceiling trails the expected horizon: the stalled-publisher discriminator.

    Zero on a healthy lane whose publisher declared its ceiling this period; growing by one a day on a
    lane whose publisher stopped, which `staleness_days` alone cannot separate from a slow provider.
    """
    if row.expected_horizon_day is None or row.source_ceiling_day is None:
        return None
    return max(0, (row.expected_horizon_day - row.source_ceiling_day) // timedelta(days=1))


__all__ = [
    "EXPECTED_HORIZON_BASIS",
    "FRESHNESS_SCHEMA_VERSION",
    "PUBLICATION_GRACE_DAYS",
    "LaneFreshness",
    "horizon_gap_days",
    "lanes_behind_provider",
    "measure_lane_freshness",
    "render_freshness_report",
    "render_lane_freshness",
    "with_freshness",
]
